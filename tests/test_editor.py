"""EditorState: todo lo que el editor hace, sin Tkinter.

La ventana (vmaxpanel/editor.py) solo ata controles a estos metodos, igual que
la bandeja con PanelApp.
"""
import json

from PIL import Image, ImageChops

from vmaxpanel.editor import EditorState, demo_sample
from vmaxpanel.metrics import METRICS, UNAVAILABLE

PROFILE = "vmaxpanel/profiles/vitals.json"


def state_for(tmp_path):
    # Se copia el archivo TAL CUAL, con su formato: los tests de guardado
    # comparan contra el original y un re-dump como linea base los invalida.
    path = tmp_path / "editando.json"
    path.write_text(open(PROFILE, encoding="utf-8").read(), encoding="utf-8")
    return EditorState(path)


def test_loads_the_profile_without_errors(tmp_path):
    st = state_for(tmp_path)
    assert st.errors == []
    assert "cpu-load" in st.widget_ids()
    assert st.dirty is False


def test_demo_sample_covers_every_metric(tmp_path):
    """Un preview lleno de "--" no sirve para disenar: el editor tiene que
    mostrar valores plausibles para las metricas que esta maquina no sirve."""
    sample = demo_sample()
    for mid in METRICS:
        assert mid in sample, mid
        assert sample[mid] is not UNAVAILABLE and sample[mid] is not None


def test_moving_a_widget_changes_the_preview(tmp_path):
    st = state_for(tmp_path)
    antes = st.preview()
    st.set_field("cpu-load", "x", "120")
    despues = st.preview()
    assert ImageChops.difference(antes.convert("RGB"),
                                despues.convert("RGB")).getbbox() is not None
    assert st.dirty is True


def test_numeric_fields_are_coerced_not_stored_as_text(tmp_path):
    st = state_for(tmp_path)
    st.set_field("cpu-load", "x", "120")
    assert st.widget("cpu-load")["x"] == 120
    st.set_field("cpu-bar", "min", "10.5")
    assert st.widget("cpu-bar")["min"] == 10.5


def test_text_fields_stay_text_even_when_they_look_numeric(tmp_path):
    """Un label con texto "6000" no puede convertirse en el entero 6000: el
    validador exige que `text` sea texto."""
    st = state_for(tmp_path)
    st.set_field("cpu-hdr", "text", "6000")
    assert st.widget("cpu-hdr")["text"] == "6000"
    assert st.errors == []


def test_an_invalid_value_reports_the_error_and_blocks_the_save(tmp_path):
    st = state_for(tmp_path)
    errores = st.set_field("cpu-load", "color", "rojo")
    assert any("color" in e for e in errores)
    assert st.save() == errores                      # no guarda
    en_disco = json.loads(st.path.read_text(encoding="utf-8"))
    assert [w for w in en_disco["widgets"]
            if w["id"] == "cpu-load"][0]["color"] != "rojo"


def test_a_valid_edit_saves_and_roundtrips(tmp_path):
    st = state_for(tmp_path)
    st.set_field("cpu-load", "color", "#00FF00")
    assert st.save() == []
    assert st.dirty is False
    otro = EditorState(st.path)
    assert otro.errors == []
    assert otro.widget("cpu-load")["color"] == "#00FF00"


def test_preview_keeps_working_while_the_layout_is_invalid(tmp_path):
    """Mientras el usuario tipea un color a medias el layout es invalido. El
    editor no puede quedarse sin preview -- se mantiene el ultimo valido, la
    misma regla que el panel."""
    st = state_for(tmp_path)
    bueno = st.preview()
    st.set_field("cpu-load", "color", "#00FF0")      # a medio tipear
    assert st.errors
    igual = st.preview()
    assert ImageChops.difference(bueno.convert("RGB"),
                                 igual.convert("RGB")).getbbox() is None


def test_reload_discards_the_unsaved_changes(tmp_path):
    st = state_for(tmp_path)
    original = st.widget("cpu-load")["color"]
    st.set_field("cpu-load", "color", "#00FF00")
    st.reload()
    assert st.widget("cpu-load")["color"] == original
    assert st.dirty is False


def test_adding_and_removing_a_widget(tmp_path):
    st = state_for(tmp_path)
    n = len(st.widget_ids())
    assert st.add_widget("rect", "sep-nuevo") == []
    assert "sep-nuevo" in st.widget_ids()
    assert len(st.widget_ids()) == n + 1
    st.remove_widget("sep-nuevo")
    assert "sep-nuevo" not in st.widget_ids()
    assert len(st.widget_ids()) == n


def test_a_new_widget_is_valid_out_of_the_box(tmp_path):
    """Agregar un widget no puede dejar el layout invalido: si el default no
    valida, el usuario ve un error que no cometio."""
    st = state_for(tmp_path)
    for tipo in ("text", "label", "bar", "arc", "graph", "rect"):
        assert st.add_widget(tipo, f"nuevo-{tipo}") == [], tipo
    assert st.errors == []
    assert st.save() == []


def test_duplicate_ids_are_rejected(tmp_path):
    st = state_for(tmp_path)
    errores = st.add_widget("rect", "cpu-load")
    assert errores
    assert st.widget("cpu-load")["type"] == "text"     # no lo piso


def test_saving_preserves_the_raw_json_structure(tmp_path):
    """Guardar reconstruyendo el modelo reescribe el archivo entero con el
    orden y el formato del serializador, y el perfil se edita a mano: el
    formato compacto de dos lineas por widget es parte del valor. Se guarda
    el dict crudo, que ademas no puede perder nada por el camino."""
    st = state_for(tmp_path)
    antes = json.loads(st.path.read_text(encoding="utf-8"))
    st.set_field("cpu-load", "color", "#00FF00")
    assert st.save() == []
    despues = json.loads(st.path.read_text(encoding="utf-8"))

    assert list(despues) == list(antes)                       # mismo orden de claves
    assert [w["id"] for w in despues["widgets"]] == [w["id"] for w in antes["widgets"]]
    uno = [w for w in despues["widgets"] if w["id"] == "cpu-load"][0]
    viejo = [w for w in antes["widgets"] if w["id"] == "cpu-load"][0]
    assert list(uno) == list(viejo)                            # y de cada widget
    assert uno["color"] == "#00FF00"


def test_saving_does_not_bloat_the_file(tmp_path):
    st = state_for(tmp_path)
    antes = len(st.path.read_text(encoding="utf-8").splitlines())
    st.set_field("cpu-load", "color", "#00FF00")
    st.save()
    despues = len(st.path.read_text(encoding="utf-8").splitlines())
    assert despues <= antes * 1.2, f"{antes} -> {despues} lineas"


# --- catalogo de metricas para el selector ---

def test_the_state_exposes_metrics_grouped_by_device(tmp_path):
    """El selector muestra "D: (JUEGOS) — libre" agrupado bajo "Disco D:", no
    una lista plana de cien ids tecnicos."""
    st = state_for(tmp_path)
    grupos = st.metric_groups()
    assert grupos, "el catalogo salio vacio"
    # cada grupo trae pares (id, etiqueta) ordenados por etiqueta
    for nombre, entradas in grupos.items():
        assert isinstance(nombre, str) and nombre
        for mid, etiqueta in entradas:
            assert isinstance(mid, str) and isinstance(etiqueta, str)
            assert etiqueta
        assert [e[1] for e in entradas] == sorted(e[1] for e in entradas)


def test_the_catalog_includes_the_metrics_the_profile_already_uses(tmp_path):
    """Si una metrica en uso no esta en el selector, el usuario no puede
    volver a elegirla despues de cambiarla."""
    st = state_for(tmp_path)
    todas = {mid for entradas in st.metric_groups().values() for mid, _ in entradas}
    for w in st.raw["widgets"]:
        if w.get("metric"):
            assert w["metric"] in todas, w["metric"]


def test_the_catalog_works_without_any_sensor_backend(tmp_path, monkeypatch):
    """El editor tiene que abrir en una maquina sin sidecar ni WMI: el
    catalogo cae a las metricas registradas."""
    import vmaxpanel.editor as ed

    def sin_nada():
        raise OSError("sin backend")

    monkeypatch.setattr(ed, "build_registry_without_sensors", sin_nada)
    st = ed.EditorState(state_for(tmp_path).path)
    grupos = st.metric_groups()
    todas = {mid for entradas in grupos.values() for mid, _ in entradas}
    assert "cpu.load" in todas and "clock.time" in todas


def test_friendly_labels_win_over_the_generic_ones(tmp_path):
    """La etiqueta del provider nombra el dispositivo real; la generica solo
    repite el id. Si esta el volumen D, su etiqueta tiene que traer la letra."""
    st = state_for(tmp_path)
    etiquetas = {mid: et for entradas in st.metric_groups().values()
                 for mid, et in entradas}
    for mid, et in etiquetas.items():
        if mid.startswith("vol."):
            assert mid.split(".")[1] + ":" in et, (mid, et)


def test_no_two_metrics_share_a_label_in_the_picker(tmp_path):
    """Dos metricas con la misma etiqueta hacen que el selector no las pueda
    distinguir: elegir una escribe la otra. Paso de verdad con disk.temp.0/1/2,
    que compartian "Temperatura de disco", y solo se veia una corrida de cada
    dos porque el orden de un set en Python varia entre procesos."""
    st = state_for(tmp_path)
    vistas = {}
    for entradas in st.metric_groups().values():
        for mid, etiqueta in entradas:
            assert etiqueta not in vistas, \
                f"{mid} y {vistas.get(etiqueta)} comparten la etiqueta {etiqueta!r}"
            vistas[etiqueta] = mid


# --- edicion del fondo y del panel ---

def test_switching_background_type_leaves_it_valid(tmp_path):
    """Cambiar de tipo tiene que dar un fondo que valide solo. Si el usuario
    elige 'procedural' y queda invalido porque faltan stops, ve un error que no
    cometio."""
    st = state_for(tmp_path)
    for tipo in ("solid", "gradient", "procedural", "sequence", "image"):
        errores = st.set_background_type(tipo)
        assert errores == [], f"{tipo}: {errores}"
        assert st.raw["background"]["type"] == tipo


def test_switching_to_procedural_keeps_the_existing_stops(tmp_path):
    """El gradiente que el usuario ya afino no se pierde al animarlo: es
    justamente el punto de que procedural parta del gradiente."""
    st = state_for(tmp_path)
    antes = [dict(s) for s in st.raw["background"]["stops"]]
    st.set_background_type("procedural")
    assert st.raw["background"]["stops"] == antes


def test_background_fields_are_coerced(tmp_path):
    st = state_for(tmp_path)
    st.set_background_type("procedural")
    assert st.set_background_field("speed", "45") == []
    assert st.raw["background"]["speed"] == 45.0
    assert st.set_background_field("name", "pulse") == []
    assert st.raw["background"]["name"] == "pulse"


def test_an_invalid_background_field_reports_and_does_not_save(tmp_path):
    st = state_for(tmp_path)
    errores = st.set_background_field("color", "verde")
    assert errores
    assert st.save() == errores


def test_stops_can_be_added_edited_and_removed(tmp_path):
    st = state_for(tmp_path)
    n = len(st.raw["background"]["stops"])
    assert st.add_stop() == []
    assert len(st.raw["background"]["stops"]) == n + 1
    assert st.set_stop(0, "color", "#FF0000") == []
    assert st.raw["background"]["stops"][0]["color"] == "#FF0000"
    assert st.set_stop(0, "at", "0.25") == []
    assert st.raw["background"]["stops"][0]["at"] == 0.25
    assert st.remove_stop(0) == []
    assert len(st.raw["background"]["stops"]) == n


def test_a_gradient_cannot_be_left_with_one_stop(tmp_path):
    """Menos de dos paradas no es un degradado: el validador lo rechaza, asi
    que borrar la penultima tiene que negarse en vez de dejar el perfil roto."""
    st = state_for(tmp_path)
    while len(st.raw["background"]["stops"]) > 2:
        st.remove_stop(0)
    errores = st.remove_stop(0)
    assert errores
    assert len(st.raw["background"]["stops"]) == 2


def test_panel_fields_are_editable_and_validated(tmp_path):
    st = state_for(tmp_path)
    assert st.set_panel_field("fps", "30") == []
    assert st.raw["panel"]["fps"] == 30
    assert st.set_panel_field("brightness", "70") == []
    errores = st.set_panel_field("fps", "120")
    assert any("fps" in e for e in errores)


def test_the_editor_publishes_the_background_fields_for_each_type(tmp_path):
    """La UI dibuja los campos que el tipo elegido admite; si los inventara,
    escribiria claves que el validador rechaza."""
    st = state_for(tmp_path)
    assert set(st.background_fields("solid")) == {"color"}
    assert "stops" not in st.background_fields("solid")
    assert set(st.background_fields("procedural")) >= {"name", "speed", "period",
                                                       "angle"}
    assert set(st.background_fields("sequence")) >= {"src", "fps", "fit"}
    assert "speed" not in st.background_fields("sequence")


# --- cajas y hit test, para arrastrar sobre la vista previa ---

def test_every_widget_has_a_bounding_box(tmp_path):
    st = state_for(tmp_path)
    for wid in st.widget_ids():
        caja = st.widget_bbox(wid)
        assert caja is not None, wid
        x0, y0, x1, y1 = caja
        assert x1 > x0 and y1 > y0, (wid, caja)


def test_a_text_box_follows_the_rendered_text(tmp_path):
    """La caja de un texto sale de medir la fuente con el valor de demo, no de
    un radio inventado: un reloj de 74 px y una etiqueta de 14 no pueden tener
    la misma zona sensible."""
    st = state_for(tmp_path)
    reloj = st.widget_bbox("clock")
    etiqueta = st.widget_bbox("cpu-temp-tag")
    alto_reloj = reloj[3] - reloj[1]
    alto_etiqueta = etiqueta[3] - etiqueta[1]
    assert alto_reloj > alto_etiqueta * 2, (alto_reloj, alto_etiqueta)


def test_a_bar_box_is_its_declared_size(tmp_path):
    st = state_for(tmp_path)
    barra = st.widget("cpu-bar")
    x0, y0, x1, y1 = st.widget_bbox("cpu-bar")
    assert (x0, y0) == (barra["x"], barra["y"])
    assert x1 - x0 == barra["w"] and y1 - y0 == barra["h"]


def test_hit_test_finds_the_widget_under_the_point(tmp_path):
    st = state_for(tmp_path)
    barra = st.widget("cpu-bar")
    centro = (barra["x"] + barra["w"] // 2, barra["y"] + barra["h"] // 2)
    assert st.hit_test(*centro) == "cpu-bar"


def test_hit_test_returns_none_on_empty_space(tmp_path):
    st = state_for(tmp_path)
    assert st.hit_test(300, 1460) is None


def test_hit_test_prefers_the_one_drawn_last(tmp_path):
    """El orden de la lista es el orden de pintado: el de arriba es el que el
    usuario ve y el que espera agarrar."""
    st = state_for(tmp_path)
    st.add_widget("rect", "tapa")
    st.set_field("tapa", "x", "24")
    st.set_field("tapa", "y", "316")
    st.set_field("tapa", "w", "272")
    st.set_field("tapa", "h", "16")
    assert st.hit_test(100, 320) == "tapa"      # tapa a cpu-bar, que esta antes


def test_dragging_moves_the_widget_to_the_point(tmp_path):
    """Arrastrar mueve por DELTA, no reposiciona la esquina en el cursor: si no,
    el widget salta al agarrarlo desde cualquier lugar que no sea su esquina."""
    st = state_for(tmp_path)
    x0, y0 = st.widget("cpu-load")["x"], st.widget("cpu-load")["y"]
    st.begin_drag("cpu-load", x0 + 10, y0 + 5)
    st.drag_to(x0 + 40, y0 + 25)
    assert st.widget("cpu-load")["x"] == x0 + 30
    assert st.widget("cpu-load")["y"] == y0 + 20
    assert st.dirty is True


def test_dragging_clamps_to_the_canvas(tmp_path):
    """Un widget arrastrado fuera del lienzo desaparece del panel y no hay
    forma de volver a agarrarlo."""
    st = state_for(tmp_path)
    st.begin_drag("cpu-load", 20, 248)
    st.drag_to(-500, -500)
    w = st.widget("cpu-load")
    assert w["x"] >= 0 and w["y"] >= 0
    st.drag_to(9999, 9999)
    ancho = st.raw["designed_for"]["width"]
    alto = st.raw["designed_for"]["height"]
    assert w["x"] < ancho and w["y"] < alto


# --- fuentes ---

def test_font_aliases_can_be_edited(tmp_path):
    st = state_for(tmp_path)
    assert st.set_font_field("hero", "size", "80") == []
    assert st.raw["fonts"]["hero"]["size"] == 80
    assert st.set_font_field("hero", "bold", "false") == []
    assert st.raw["fonts"]["hero"]["bold"] is False


def test_adding_a_font_alias_is_valid_out_of_the_box(tmp_path):
    st = state_for(tmp_path)
    assert st.add_font("titulo") == []
    assert "titulo" in st.raw["fonts"]
    assert st.save() == []


def test_a_duplicate_font_alias_is_rejected(tmp_path):
    st = state_for(tmp_path)
    assert st.add_font("hero")


def test_a_font_in_use_cannot_be_removed(tmp_path):
    """Borrar un alias que algun widget usa deja el layout invalido: el
    validador rechaza el perfil y el panel se queda con el anterior."""
    st = state_for(tmp_path)
    errores = st.remove_font("hero")
    assert errores and any("hero" in e for e in errores)
    assert "hero" in st.raw["fonts"]


def test_an_unused_font_can_be_removed(tmp_path):
    st = state_for(tmp_path)
    st.add_font("sin-usar")
    assert st.remove_font("sin-usar") == []
    assert "sin-usar" not in st.raw["fonts"]


def test_the_available_families_are_offered(tmp_path):
    """El combo de familia se llena con las fuentes instaladas: tipear el
    nombre a mano es como se escribe una familia que no existe y el widget
    termina con la fuente por defecto sin avisar."""
    st = state_for(tmp_path)
    familias = st.font_families()
    assert familias, "no encontro ninguna familia instalada"
    assert any("consol" in f.lower() for f in familias)
    for f in familias:
        assert isinstance(f, str) and f


# --- deshacer ---

def test_undo_reverts_the_last_change(tmp_path):
    st = state_for(tmp_path)
    original = st.widget("cpu-load")["x"]
    st.set_field("cpu-load", "x", "99")
    assert st.widget("cpu-load")["x"] == 99
    assert st.undo() is True
    assert st.widget("cpu-load")["x"] == original


def test_undo_with_nothing_to_undo_says_so(tmp_path):
    st = state_for(tmp_path)
    assert st.undo() is False


def test_undo_walks_back_several_steps(tmp_path):
    st = state_for(tmp_path)
    original = st.widget("cpu-load")["x"]
    for v in (10, 20, 30):
        st.set_field("cpu-load", "x", str(v))
    st.undo()
    assert st.widget("cpu-load")["x"] == 20
    st.undo()
    assert st.widget("cpu-load")["x"] == 10
    st.undo()
    assert st.widget("cpu-load")["x"] == original


def test_a_whole_drag_is_one_undo_step(tmp_path):
    """Arrastrar dispara un cambio por pixel de mouse. Si cada uno fuera un paso,
    deshacer un arrastre pediria cincuenta Ctrl+Z."""
    st = state_for(tmp_path)
    x0 = st.widget("cpu-load")["x"]
    st.begin_drag("cpu-load", x0, 248)
    for dx in range(1, 40):
        st.drag_to(x0 + dx, 248)
    st.end_drag()
    assert st.widget("cpu-load")["x"] != x0
    assert st.undo() is True
    assert st.widget("cpu-load")["x"] == x0


def test_undo_covers_adding_and_removing_widgets(tmp_path):
    st = state_for(tmp_path)
    n = len(st.widget_ids())
    st.add_widget("rect", "nuevo")
    st.undo()
    assert len(st.widget_ids()) == n
    st.remove_widget("cpu-load")
    st.undo()
    assert st.widget("cpu-load") is not None


def test_undo_covers_the_background_and_the_fonts(tmp_path):
    st = state_for(tmp_path)
    tipo = st.raw["background"]["type"]
    st.set_background_type("solid")
    st.undo()
    assert st.raw["background"]["type"] == tipo
    st.add_font("prueba")
    st.undo()
    assert "prueba" not in st.raw["fonts"]


def test_the_undo_history_is_bounded(tmp_path):
    """Un historial sin limite guarda una copia del layout por cada pixel de
    arrastre: son 300 KB por copia con 154 widgets."""
    st = state_for(tmp_path)
    for i in range(200):
        st.set_field("cpu-load", "x", str(20 + i % 50))
    assert len(st._historial) <= st.MAX_UNDO


# --- reglas de color ---

def test_rules_are_listed_parsed(tmp_path):
    """El JSON guarda la regla como "> 90"; la UI necesita el operador y el
    numero por separado para poner un combo y un campo."""
    st = state_for(tmp_path)
    reglas = st.rules("cpu-load")
    assert reglas and reglas[0]["op"] == ">" and reglas[0]["value"] == "90"
    assert reglas[0]["color"].startswith("#")


def test_a_widget_without_rules_reports_an_empty_list(tmp_path):
    st = state_for(tmp_path)
    assert st.rules("cpu-bar") == []
    assert st.rules("no-existe") == []


def test_adding_a_rule_gives_something_valid(tmp_path):
    st = state_for(tmp_path)
    assert st.add_rule("cpu-temp") == []
    assert len(st.rules("cpu-temp")) == 2
    assert st.save() == []


def test_editing_the_pieces_of_a_rule(tmp_path):
    st = state_for(tmp_path)
    assert st.set_rule("cpu-load", 0, "op", ">=") == []
    assert st.set_rule("cpu-load", 0, "value", "75.5") == []
    assert st.set_rule("cpu-load", 0, "color", "#00FF00") == []
    r = st.rules("cpu-load")[0]
    assert (r["op"], r["value"], r["color"]) == (">=", "75.5", "#00FF00")
    # y en el JSON quedo como el comparador que el validador espera
    crudo = st.widget("cpu-load")["rules"][0]
    assert crudo["when"] == ">= 75.5"


def test_an_impossible_rule_is_reported_not_written_silently(tmp_path):
    st = state_for(tmp_path)
    errores = st.set_rule("cpu-load", 0, "value", "no es un numero")
    assert errores and any("rules" in e or "comparador" in e for e in errores)


def test_a_bad_operator_is_refused(tmp_path):
    st = state_for(tmp_path)
    assert st.set_rule("cpu-load", 0, "op", "=~")
    assert st.rules("cpu-load")[0]["op"] == ">"


def test_removing_a_rule(tmp_path):
    st = state_for(tmp_path)
    n = len(st.rules("cpu-load"))
    assert st.remove_rule("cpu-load", 0) == []
    assert len(st.rules("cpu-load")) == n - 1


def test_rules_are_undoable(tmp_path):
    st = state_for(tmp_path)
    n = len(st.rules("cpu-load"))
    st.add_rule("cpu-load")
    st.undo()
    assert len(st.rules("cpu-load")) == n


def test_the_operators_offered_are_the_ones_the_validator_accepts(tmp_path):
    st = state_for(tmp_path)
    for op in st.rule_operators():
        assert st.set_rule("cpu-load", 0, "op", op) == [], op
