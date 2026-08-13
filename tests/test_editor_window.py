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
    """Devuelve la ventana con el perfil como recien abierto.

    Ademas del perfil se resetean las dos cosas de la VENTANA que un test puede
    dejar cambiadas y otro leer: el tamano y donde esta el foco. Compartir una sola
    raiz de Tk es necesario (ver _ventana) pero convierte cualquier estado global en
    una dependencia de orden -- con pytest-randomly, en fallas que aparecen y
    desaparecen segun la semilla. Resetear aca es mas barato que perseguirlas de a
    una.
    """
    _ventana.state.path.write_text(ORIGINAL, encoding="utf-8")
    _ventana._discard()
    _ventana.root.geometry("1200x900")
    _ventana.lista.focus_set()
    _ventana.root.update()
    return _ventana


def metrica_mostrada(w):
    """El id de metrica que el selector esta mostrando ahora.

    El combo muestra la etiqueta amigable, no el id: el mapeo inverso es lo
    que la ventana usa para escribir en el estado."""
    combo = w._pickers.get("metric")
    if combo is None:
        return None
    return w._metric_por_etiqueta.get(combo.get())


def seleccionar(w, wid):
    ids = w.state.widget_ids()
    w.lista.selection_clear(0, "end")
    w.lista.selection_set(ids.index(wid))
    w.lista.event_generate("<<ListboxSelect>>")
    w.root.update()


def test_selecting_a_widget_shows_its_own_properties(ventana):
    seleccionar(ventana, "ssd-2")
    assert ventana._selected() == "ssd-2"
    assert metrica_mostrada(ventana) == "disk.temp.2"
    assert ventana._fields["x"].get() == "162"


def test_switching_selection_replaces_the_properties(ventana):
    seleccionar(ventana, "clock")
    assert metrica_mostrada(ventana) == "clock.time"
    seleccionar(ventana, "cpu-bar")
    assert metrica_mostrada(ventana) == "cpu.load"
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
    assert metrica_mostrada(ventana) == "cpu.load"


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


def test_the_preview_grows_with_the_window(ventana):
    """Una escala fija desperdicia una ventana maximizada: el panel es
    320x1480 y en 0.36 se ve en miniatura. La vista previa tiene que usar el
    alto disponible."""
    ventana.root.geometry("900x600")
    ventana.root.update()
    chico = ventana._preview_img.height()
    escala_chica = ventana._escala

    ventana.root.geometry("1400x1000")
    ventana.root.update()
    grande = ventana._preview_img.height()

    assert grande > chico, f"{chico} -> {grande} px de alto"
    assert ventana._escala > escala_chica


def test_the_preview_keeps_the_panel_aspect_ratio(ventana):
    ventana.root.geometry("1400x1000")
    ventana.root.update()
    ancho, alto = ventana._preview_img.width(), ventana._preview_img.height()
    assert abs(alto / ancho - 1480 / 320) < 0.05


def test_resizing_does_not_loop_forever(ventana):
    """Cambiar la imagen cambia el tamano del Label, que dispara otro
    <Configure>: sin una guarda eso es un bucle de redibujo infinito."""
    ventana._redibujos = 0
    original = ventana._draw_preview

    def contar():
        ventana._redibujos += 1
        original()

    ventana._draw_preview = contar
    ventana.root.geometry("1200x900")
    for _ in range(6):
        ventana.root.update()
    assert ventana._redibujos <= 3, f"{ventana._redibujos} redibujos por un resize"


def test_the_metric_field_is_a_picker_with_friendly_labels(ventana):
    """El campo `metric` era texto libre: habia que saber de memoria que
    existe "vol.D.free". Ahora es una lista con nombres amigables agrupados
    por dispositivo."""
    seleccionar(ventana, "cpu-load")
    combo = ventana._pickers.get("metric")
    assert combo is not None, "metric sigue siendo un campo de texto"
    valores = list(combo.cget("values"))
    assert valores, "el selector salio vacio"
    # el valor mostrado es la etiqueta amigable, no el id
    assert combo.get() != "cpu.load"
    assert "CPU" in combo.get() or "carga" in combo.get().lower()
    # y los grupos aparecen como encabezados no seleccionables
    assert any(v.startswith("——") for v in valores), "no hay encabezados de grupo"


def test_choosing_from_the_picker_sets_the_metric_id(ventana):
    seleccionar(ventana, "cpu-load")
    combo = ventana._pickers["metric"]
    objetivo = next(v for v in combo.cget("values")
                    if not v.startswith("——") and "GPU" in v.upper())
    combo.set(objetivo)
    ventana._on_pick_metric()
    assert ventana.state.widget("cpu-load")["metric"].startswith("gpu.")
    assert ventana.state.dirty is True


def test_a_group_header_is_not_selectable_as_a_metric(ventana):
    """Elegir un encabezado no puede escribir "—— CPU ——" como metrica."""
    seleccionar(ventana, "cpu-load")
    antes = ventana.state.widget("cpu-load")["metric"]
    combo = ventana._pickers["metric"]
    combo.set(next(v for v in combo.cget("values") if v.startswith("——")))
    ventana._on_pick_metric()
    assert ventana.state.widget("cpu-load")["metric"] == antes
    assert ventana.state.errors == []


def test_the_window_has_tabs_for_widgets_background_and_panel(ventana):
    """El fondo, las fuentes y el panel no son widgets: meterlos en la misma
    columna obligaria a elegir entre ver la lista o ver el fondo."""
    pestanas = [ventana.tabs.tab(i, "text") for i in range(len(ventana.tabs.tabs()))]
    assert pestanas == ["Widgets", "Background", "Fonts", "Panel"]


def test_the_background_tab_shows_the_fields_of_the_current_type(ventana):
    ventana._show_background()
    ventana.root.update()
    assert ventana._bg_type.get() == "gradient"
    assert "angle" in ventana._bg_fields
    assert "speed" not in ventana._bg_fields        # gradient no anima


def test_switching_the_type_in_the_ui_redraws_the_fields(ventana):
    ventana._bg_type.set("procedural")
    ventana._on_pick_bg_type()
    ventana.root.update()
    assert ventana.state.raw["background"]["type"] == "procedural"
    assert {"name", "speed", "period"} <= set(ventana._bg_fields)
    assert ventana.state.errors == []


def test_the_stops_editor_lists_one_row_per_stop(ventana):
    ventana._show_background()
    ventana.root.update()
    assert len(ventana._stop_rows) == len(ventana.state.stops())
    ventana._add_stop()
    ventana.root.update()
    assert len(ventana._stop_rows) == len(ventana.state.stops())


def test_editing_a_stop_from_the_ui_reaches_the_state(ventana):
    ventana._show_background()
    ventana.root.update()
    fila = ventana._stop_rows[0]
    fila["color"].set("#FF0000")
    ventana._apply_stop(0, "color")
    assert ventana.state.stops()[0]["color"] == "#FF0000"
    assert ventana.state.dirty is True


def test_the_panel_tab_edits_fps(ventana):
    ventana._show_panel()
    ventana.root.update()
    ventana._panel_fields["fps"].set("30")
    ventana._apply_panel("fps")
    assert ventana.state.raw["panel"]["fps"] == 30


def test_an_animated_background_shows_a_hint_about_the_preview(ventana):
    """La vista previa es un cuadro fijo: si el fondo se mueve, el usuario
    tiene que saber que lo que ve no es una animacion detenida por un bug."""
    ventana._bg_type.set("procedural")
    ventana._on_pick_bg_type()
    ventana.root.update()
    assert "animate" in ventana._bg_hint.cget("text").lower()


def test_clicking_the_preview_selects_that_widget(ventana):
    """Hacer clic sobre el panel dibujado es la forma natural de elegir: la
    lista de 47 nombres obliga a saber de memoria como se llama cada cosa."""
    ventana.root.geometry("1200x900")
    ventana.root.update()
    barra = ventana.state.widget("cpu-bar")
    px, py = ventana._a_pantalla(barra["x"] + 100, barra["y"] + 8)
    ventana.canvas.event_generate("<Button-1>", x=px, y=py)
    ventana.root.update()
    assert ventana._selected() == "cpu-bar"
    assert metrica_mostrada(ventana) == "cpu.load"


def test_dragging_on_the_preview_moves_the_widget(ventana):
    ventana.root.geometry("1200x900")
    ventana.root.update()
    w = ventana.state.widget("cpu-load")
    x0, y0 = w["x"], w["y"]
    px, py = ventana._a_pantalla(x0 + 5, y0 + 20)
    ventana.canvas.event_generate("<Button-1>", x=px, y=py)
    destino = ventana._a_pantalla(x0 + 45, y0 + 20)
    ventana.canvas.event_generate("<B1-Motion>", x=destino[0], y=destino[1])
    ventana.canvas.event_generate("<ButtonRelease-1>", x=destino[0], y=destino[1])
    ventana.root.update()
    assert ventana.state.widget("cpu-load")["x"] > x0 + 20
    assert ventana.state.dirty is True


def test_clicking_empty_space_keeps_the_selection(ventana):
    """Un clic al vacio no puede deseleccionar: el panel de propiedades se
    vaciaria y el usuario perderia lo que estaba editando."""
    seleccionar(ventana, "cpu-load")
    ventana.root.geometry("1200x900")
    ventana.root.update()
    px, py = ventana._a_pantalla(310, 1470)
    ventana.canvas.event_generate("<Button-1>", x=px, y=py)
    ventana.root.update()
    assert ventana._selected() == "cpu-load"


def test_screen_and_panel_coordinates_round_trip(ventana):
    ventana.root.geometry("1200x900")
    ventana.root.update()
    for punto in ((0, 0), (100, 400), (319, 1479)):
        px, py = ventana._a_pantalla(*punto)
        vuelta = ventana._a_panel(px, py)
        assert abs(vuelta[0] - punto[0]) <= 2, (punto, vuelta)
        assert abs(vuelta[1] - punto[1]) <= 2, (punto, vuelta)


def test_there_is_a_fonts_tab(ventana):
    pestanas = [ventana.tabs.tab(i, "text") for i in range(len(ventana.tabs.tabs()))]
    assert pestanas == ["Widgets", "Background", "Fonts", "Panel"]


def test_the_fonts_tab_lists_one_row_per_alias(ventana):
    ventana._show_fonts()
    ventana.root.update()
    assert set(ventana._font_rows) == set(ventana.state.fonts())


def test_editing_a_font_size_from_the_ui_reaches_the_state(ventana):
    ventana._show_fonts()
    ventana.root.update()
    ventana._font_rows["tag"]["size"].set("18")
    ventana._apply_font("tag", "size")
    assert ventana.state.raw["fonts"]["tag"]["size"] == 18
    assert ventana.state.dirty is True


def test_removing_a_font_in_use_reports_instead_of_breaking(ventana):
    ventana._show_fonts()
    ventana.root.update()
    ventana._remove_font("hero")
    assert "hero" in ventana.state.raw["fonts"]
    assert "hero" in ventana.estado.cget("text")


def test_the_family_picker_offers_installed_families(ventana):
    ventana._show_fonts()
    ventana.root.update()
    combo = ventana._font_rows["hero"]["family_combo"]
    valores = list(combo.cget("values"))
    assert len(valores) > 5, "el combo de familias salio casi vacio"
    assert any("consol" in v.lower() for v in valores)


def test_control_z_undoes_from_the_window(ventana):
    seleccionar(ventana, "cpu-load")
    x0 = ventana.state.widget("cpu-load")["x"]
    ventana._move(20, 0)
    assert ventana.state.widget("cpu-load")["x"] == x0 + 20
    # focus_force antes del event_generate: una tecla va al widget con foco, y en
    # una ventana sin foco Tk la descarta sin avisar. Sin esto el test pasaba o
    # fallaba segun que tenia el foco la maquina en ese instante -- y cuando
    # fallaba, el diff era "no deshizo", que apunta al codigo en vez de al test.
    # Es el mismo agujero que ya me hizo concluir mal una vez, con
    # <<ListboxSelect>> sobre una ventana no mapeada.
    ventana.root.focus_force()
    ventana.root.update()
    ventana.root.event_generate("<Control-z>", when="now")
    ventana.root.update()
    assert ventana.state.widget("cpu-load")["x"] == x0


def test_undo_refreshes_the_fields_and_the_preview(ventana):
    """Deshacer sin repintar deja los campos mostrando el valor deshecho: el
    usuario ve un numero que ya no es el del layout."""
    seleccionar(ventana, "cpu-load")
    ventana._fields["x"].set("222")
    ventana._apply("x")
    ventana._undo()
    ventana.root.update()
    assert ventana._fields["x"].get() != "222"
    assert ventana._fields["x"].get() == str(ventana.state.widget("cpu-load")["x"])


def test_undo_with_empty_history_says_so_in_the_status(ventana):
    ventana._undo()
    assert "undo" in ventana.estado.cget("text").lower()


def test_the_rules_editor_appears_only_for_text_widgets(ventana):
    """Solo los widgets de texto tienen reglas de color en este motor: mostrar
    la seccion en una barra prometeria algo que no existe."""
    seleccionar(ventana, "cpu-load")
    assert ventana._rule_rows, "un text con reglas no mostro el editor"
    seleccionar(ventana, "cpu-bar")
    assert ventana._rule_rows == []


def test_editing_a_rule_from_the_ui_reaches_the_state(ventana):
    seleccionar(ventana, "cpu-load")
    fila = ventana._rule_rows[0]
    fila["value"].set("70")
    ventana._apply_rule(0, "value")
    assert ventana.state.rules("cpu-load")[0]["value"] == "70"
    assert ventana.state.dirty is True


def test_adding_and_removing_a_rule_from_the_ui(ventana):
    seleccionar(ventana, "cpu-temp")
    n = len(ventana._rule_rows)
    ventana._add_rule()
    assert len(ventana._rule_rows) == n + 1
    ventana._remove_rule(n)
    assert len(ventana._rule_rows) == n


def test_a_rule_that_would_break_the_layout_is_reported(ventana):
    seleccionar(ventana, "cpu-load")
    fila = ventana._rule_rows[0]
    fila["value"].set("no")
    ventana._apply_rule(0, "value")
    assert ventana.state.errors == []            # se revirtio
    assert ventana.estado.cget("text")           # y lo dijo


# --- exportar e importar desde el editor ---


def test_exporting_from_the_editor_writes_a_bundle(ventana, tmp_path):
    destino = tmp_path / "salida.vmaxpanel"
    ventana._exportar_a(destino)
    assert destino.exists()
    assert "salida.vmaxpanel" in ventana.estado.cget("text")


def test_exporting_with_unsaved_changes_refuses_and_says_why(ventana, tmp_path):
    """Exportar lee el archivo del disco. Con cambios sin guardar, el bundle
    llevaria la version vieja y el usuario compartiria algo que no es lo que ve en
    pantalla -- el peor tipo de error, porque no se nota hasta que alguien mas lo
    abre."""
    seleccionar(ventana, "cpu-load")
    ventana._move(5, 0)
    destino = tmp_path / "no-deberia.vmaxpanel"
    ventana._exportar_a(destino)
    assert not destino.exists()
    assert "unsaved" in ventana.estado.cget("text").lower()


def test_exporting_over_an_existing_file_refuses(ventana, tmp_path):
    destino = tmp_path / "ya-esta.vmaxpanel"
    destino.write_bytes(b"algo")
    ventana._exportar_a(destino)
    assert destino.read_bytes() == b"algo"
    assert "ya existe" in ventana.estado.cget("text")


def test_importing_from_the_editor_loads_the_imported_profile(ventana, tmp_path):
    """Importar y no abrir el perfil importado dejaria al usuario adivinando si
    funciono. Se importa y se pasa a editarlo."""
    from vmaxpanel import bundle
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(ventana.state.path, zip_,
                          assets_dir=tmp_path / "assets-vacio")
    destino_p = tmp_path / "perfiles"
    ventana._importar_de(zip_, profiles_dir=destino_p, assets_dir=tmp_path / "a2")
    assert ventana.state.path.parent == destino_p
    assert ventana.state.raw["name"]
    assert "importado" in ventana.estado.cget("text").lower()


def test_a_bad_bundle_reports_in_the_status_bar_and_keeps_editing(ventana, tmp_path):
    falso = tmp_path / "x.vmaxpanel"
    falso.write_bytes(b"no soy un zip")
    antes = ventana.state.path
    ventana._importar_de(falso, profiles_dir=tmp_path / "p", assets_dir=tmp_path / "a")
    assert ventana.state.path == antes
    assert "not a readable bundle" in ventana.estado.cget("text")


# --- la barra de acciones tiene que estar en todas las pestañas ---


def test_the_save_button_is_not_trapped_inside_the_widgets_tab(ventana):
    """Estaba dentro de la pestaña Widgets, así que editando el fondo no había
    ningún botón de guardar ni barra de estado: el usuario cambiaba el fondo, no
    encontraba dónde aplicarlo, reiniciaba el motor y el cambio se perdía. Reportado
    tal cual: "no hay boton de aplicar ni guarda"."""
    assert not str(ventana._acciones).startswith(str(ventana.tabs) + ".")
    assert not str(ventana.estado).startswith(str(ventana.tabs) + ".")


def tipear(ventana, padre, var, valor):
    """Simula escribir en un campo: el texto queda en el control y NO se aplica --
    ese es el punto, aplicar es lo que el usuario nunca hizo. El editor lo detecta
    con un trace sobre la variable, asi que cambiarla es exactamente lo que pasa al
    teclear."""
    entradas = [c for c in padre.winfo_children()
                if c.winfo_class() in ("TEntry", "Entry")]
    assert entradas, "ese panel no tiene campos de texto"
    var.set(valor)
    ventana.root.update()


def test_changing_the_background_and_saving_persists_it(ventana):
    """El camino que falló: escribir en un campo del fondo y darle a Guardar, SIN
    pasar por Enter. Los Entry sólo aplican con <Return> o <FocusOut>, así que el
    valor recién tipeado no llegaba al estado y se guardaba el viejo."""
    ventana.tabs.select(1)                       # pestaña Fondo
    ventana._bg_type.set("solid")
    ventana._on_pick_bg_type()                   # el camino del combo, con su poda
    ventana.root.update()

    tipear(ventana, ventana._bg_campos, ventana._bg_fields["color"], "#123456")
    ventana._save()

    en_disco = json.loads(ventana.state.path.read_text(encoding="utf-8"))
    assert en_disco["background"]["color"] == "#123456"


def test_a_pending_widget_field_is_committed_on_save(ventana):
    seleccionar(ventana, "cpu-load")
    tipear(ventana, ventana.props, ventana._fields["x"], "77")
    ventana._save()
    en_disco = json.loads(ventana.state.path.read_text(encoding="utf-8"))
    assert [w for w in en_disco["widgets"]
            if w["id"] == "cpu-load"][0]["x"] == 77


def test_discarding_also_drops_what_was_typed_but_not_applied(ventana):
    """Si lo tipeado quedara pendiente, reaparecería en el próximo guardado — o sea
    que "Descartar cambios" no descartaría nada."""
    seleccionar(ventana, "cpu-load")
    x0 = ventana.state.widget("cpu-load")["x"]
    tipear(ventana, ventana.props, ventana._fields["x"], "999")
    ventana._discard()
    ventana._save()
    en_disco = json.loads(ventana.state.path.read_text(encoding="utf-8"))
    assert [w for w in en_disco["widgets"]
            if w["id"] == "cpu-load"][0]["x"] == x0


def test_unsaved_changes_are_visible_in_the_title(ventana):
    """Sin señal de "hay cambios sin guardar", reiniciar el motor desde la bandeja
    parece ignorar la edición -- y en realidad la edición nunca llegó al disco."""
    assert "unsaved" not in ventana.root.title()
    seleccionar(ventana, "cpu-load")
    ventana._move(3, 0)
    assert "unsaved" in ventana.root.title()
    ventana._save()
    assert "unsaved" not in ventana.root.title()


# --- elegir el archivo del fondo ---


def test_choosing_an_asset_outside_the_project_copies_it_in(ventana, tmp_path):
    """`safe_asset_path` rechaza cualquier cosa fuera de vmaxpanel/assets, así que
    elegir un video del Escritorio sólo puede funcionar copiándolo adentro. Sin esto
    el usuario elige un archivo, el editor guarda una ruta que el motor rechaza, y el
    fondo queda en color plano sin explicación."""
    origen = tmp_path / "mi video.mp4"
    origen.write_bytes(b"contenido")
    assets = tmp_path / "assets"
    assets.mkdir()

    nombre = ventana._usar_asset(origen, assets_dir=assets)

    assert nombre == "mi video.mp4"
    assert (assets / "mi video.mp4").read_bytes() == b"contenido"
    assert ventana.state.raw["background"]["src"] == "mi video.mp4"


def test_choosing_an_asset_already_inside_does_not_duplicate_it(ventana, tmp_path):
    assets = tmp_path / "assets"
    (assets / "fondos").mkdir(parents=True)
    dentro = assets / "fondos" / "ya-esta.mp4"
    dentro.write_bytes(b"x")

    nombre = ventana._usar_asset(dentro, assets_dir=assets)

    assert nombre == "fondos/ya-esta.mp4"          # relativo, con / para el JSON
    assert list((assets / "fondos").iterdir()) == [dentro]


def test_a_name_collision_with_other_content_does_not_overwrite(ventana, tmp_path):
    """Copiar encima del asset de otro perfil es destruir trabajo ajeno por un
    nombre repetido."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "fondo.mp4").write_bytes(b"el que ya estaba")
    otro = tmp_path / "otra carpeta" / "fondo.mp4"
    otro.parent.mkdir()
    otro.write_bytes(b"el nuevo")

    nombre = ventana._usar_asset(otro, assets_dir=assets)

    assert nombre == "fondo-2.mp4"
    assert (assets / "fondo.mp4").read_bytes() == b"el que ya estaba"
    assert (assets / "fondo-2.mp4").read_bytes() == b"el nuevo"


def test_choosing_the_same_file_twice_reuses_the_copy(ventana, tmp_path):
    """Mismo contenido y mismo nombre: es el mismo archivo, no hace falta otra
    copia. Sin esto, tocar el botón dos veces deja fondo.mp4 y fondo-2.mp4
    idénticos."""
    assets = tmp_path / "assets"
    assets.mkdir()
    origen = tmp_path / "v.mp4"
    origen.write_bytes(b"igual")

    primero = ventana._usar_asset(origen, assets_dir=assets)
    segundo = ventana._usar_asset(origen, assets_dir=assets)

    assert primero == segundo == "v.mp4"
    assert [p.name for p in assets.iterdir()] == ["v.mp4"]


def test_choosing_a_folder_for_a_sequence_copies_the_whole_folder(ventana, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    carpeta = tmp_path / "cuadros"
    carpeta.mkdir()
    for i in range(3):
        (carpeta / f"{i}.png").write_bytes(b"x")

    nombre = ventana._usar_asset(carpeta, assets_dir=assets)

    assert nombre == "cuadros"
    assert len(list((assets / "cuadros").iterdir())) == 3


def test_a_missing_asset_is_reported_and_changes_nothing(ventana, tmp_path):
    antes = dict(ventana.state.raw["background"])
    nombre = ventana._usar_asset(tmp_path / "no-existe.mp4",
                                 assets_dir=tmp_path / "assets")
    assert nombre is None
    assert ventana.state.raw["background"] == antes
    assert "no-existe.mp4" in ventana.estado.cget("text")


def test_the_choose_button_appears_only_for_backgrounds_with_a_file(ventana):
    """Un `solid` no tiene archivo que elegir; un `video` sí. El botón sigue al
    campo `src`, así que aparece exactamente cuando ese campo existe."""
    def hay_boton():
        return any("Choose" in str(c.cget("text"))
                   for c in ventana._bg_campos.winfo_children()
                   if c.winfo_class() == "TButton")

    ventana._bg_type.set("solid")
    ventana._on_pick_bg_type()
    ventana.root.update()
    assert not hay_boton()

    ventana._bg_type.set("video")
    ventana._on_pick_bg_type()
    ventana.root.update()
    assert hay_boton()


def test_the_window_opens_big_enough_to_see_the_preview(ventana):
    """Al abrir, la ventana venia con el tamano que Tkinter le diera: la lista de
    widgets y las propiedades se comian el ancho y la vista previa quedaba en una
    tirita de ~60 px para un panel de 320x1480. El usuario ya se habia quejado de ver
    todo en miniatura y lo arregle para cuando MAXIMIZAS -- pero recien abierta seguia
    mal, que es el 100% de las veces que se abre."""
    ventana.root.update()
    alto_pantalla = ventana.root.winfo_screenheight()
    pedido = ventana._geometria_inicial(1920, alto_pantalla)
    ancho, alto = (int(v) for v in pedido.split("+")[0].split("x"))
    assert alto >= min(900, int(alto_pantalla * 0.8)), pedido
    assert ancho >= 1000, pedido
    # y no mas grande que la pantalla, que dejaria los botones del pie fuera de vista
    assert alto <= alto_pantalla
    assert ancho <= 1920


def test_the_initial_size_fits_a_small_screen(ventana):
    """En una pantalla de notebook 1366x768 no puede pedir 950 de alto: la barra de
    acciones quedaria abajo del borde y no habria como guardar."""
    pedido = ventana._geometria_inicial(1366, 768)
    ancho, alto = (int(v) for v in pedido.split("+")[0].split("x"))
    assert ancho <= 1366 and alto <= 768
