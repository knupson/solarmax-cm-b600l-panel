"""EditorState: everything the editor does, without Tkinter.

The window (vmaxpanel/editor.py) only wires controls to these methods, the same
way the tray does with PanelApp.
"""
import json

import pytest

from PIL import Image, ImageChops

from vmaxpanel.editor import EditorState, demo_sample
from vmaxpanel.metrics import METRICS, UNAVAILABLE

PROFILE = "vmaxpanel/profiles/vitals.json"


def state_for(tmp_path):
    # The file is copied AS IS, with its formatting: the save tests compare against
    # the original and a re-dump as the baseline would invalidate them.
    path = tmp_path / "editando.json"
    path.write_text(open(PROFILE, encoding="utf-8").read(), encoding="utf-8")
    return EditorState(path)


def test_loads_the_profile_without_errors(tmp_path):
    st = state_for(tmp_path)
    assert st.errors == []
    assert "cpu-load" in st.widget_ids()
    assert st.dirty is False


def test_demo_sample_covers_every_metric(tmp_path):
    """A preview full of "--" is useless for designing: the editor has to show
    plausible values for the metrics this machine does not serve."""
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
    """A label with the text "6000" must not become the integer 6000: the validator
    requires `text` to be a string."""
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
    """While the user is halfway through typing a colour the layout is invalid. The
    editor cannot be left without a preview -- the last valid one is kept, the same
    rule as the panel."""
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
    """Adding a widget must not leave the layout invalid: if the default does not
    validate, the user sees an error they did not make."""
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
    """Saving by rebuilding the model rewrites the whole file in the serialiser's
    order and formatting, and the profile is edited by hand: the compact
    two-lines-per-widget layout is part of the value. The raw dict is saved, which
    also cannot lose anything along the way."""
    st = state_for(tmp_path)
    antes = json.loads(st.path.read_text(encoding="utf-8"))
    st.set_field("cpu-load", "color", "#00FF00")
    assert st.save() == []
    despues = json.loads(st.path.read_text(encoding="utf-8"))

    assert list(despues) == list(antes)                       # the same key order
    assert [w["id"] for w in despues["widgets"]] == [w["id"] for w in antes["widgets"]]
    uno = [w for w in despues["widgets"] if w["id"] == "cpu-load"][0]
    viejo = [w for w in antes["widgets"] if w["id"] == "cpu-load"][0]
    assert list(uno) == list(viejo)                            # and of each widget
    assert uno["color"] == "#00FF00"


def test_saving_does_not_bloat_the_file(tmp_path):
    st = state_for(tmp_path)
    antes = len(st.path.read_text(encoding="utf-8").splitlines())
    st.set_field("cpu-load", "color", "#00FF00")
    st.save()
    despues = len(st.path.read_text(encoding="utf-8").splitlines())
    assert despues <= antes * 1.2, f"{antes} -> {despues} lineas"


# --- the metric catalogue for the selector ---

def test_the_state_exposes_metrics_grouped_by_device(tmp_path):
    """El selector muestra "D: (JUEGOS) — libre" agrupado bajo "Disco D:", no
    a flat list of a hundred technical ids."""
    st = state_for(tmp_path)
    grupos = st.metric_groups()
    assert grupos, "the catalogue came out empty"
    # each group carries (id, label) pairs sorted by label
    for nombre, entradas in grupos.items():
        assert isinstance(nombre, str) and nombre
        for mid, etiqueta in entradas:
            assert isinstance(mid, str) and isinstance(etiqueta, str)
            assert etiqueta
        assert [e[1] for e in entradas] == sorted(e[1] for e in entradas)


def test_the_catalog_includes_the_metrics_the_profile_already_uses(tmp_path):
    """If a metric in use is not in the selector, the user cannot
    volver a elegirla despues de cambiarla."""
    st = state_for(tmp_path)
    todas = {mid for entradas in st.metric_groups().values() for mid, _ in entradas}
    for w in st.raw["widgets"]:
        if w.get("metric"):
            assert w["metric"] in todas, w["metric"]


def test_the_catalog_works_without_any_sensor_backend(tmp_path, monkeypatch):
    """The editor has to open on a machine with neither a sidecar nor WMI: the
    catalogue falls back to the registered metrics."""
    import vmaxpanel.editor as ed

    def sin_nada():
        raise OSError("no backend")

    monkeypatch.setattr(ed, "build_registry_without_sensors", sin_nada)
    st = ed.EditorState(state_for(tmp_path).path)
    grupos = st.metric_groups()
    todas = {mid for entradas in grupos.values() for mid, _ in entradas}
    assert "cpu.load" in todas and "clock.time" in todas


def test_friendly_labels_win_over_the_generic_ones(tmp_path):
    """The provider's label names the real device; the generic one just repeats the
    id. If volume D is present, its label has to carry the letter."""
    st = state_for(tmp_path)
    etiquetas = {mid: et for entradas in st.metric_groups().values()
                 for mid, et in entradas}
    for mid, et in etiquetas.items():
        if mid.startswith("vol."):
            assert mid.split(".")[1] + ":" in et, (mid, et)


def test_no_two_metrics_share_a_label_in_the_picker(tmp_path):
    """Two metrics with the same label make the selector unable to tell them apart:
    choosing one writes the other. It really happened with disk.temp.0/1/2, which
    shared "Disk temperature", and it only showed on every other run because a
    Python set's ordering varies between processes."""
    st = state_for(tmp_path)
    vistas = {}
    for entradas in st.metric_groups().values():
        for mid, etiqueta in entradas:
            assert etiqueta not in vistas, \
                f"{mid} and {vistas.get(etiqueta)} share the label {etiqueta!r}"
            vistas[etiqueta] = mid


# --- editing the background and the panel ---

def test_switching_background_type_leaves_it_valid(tmp_path):
    """Changing type has to produce a background that validates on its own. If the
    user picks 'procedural' and it ends up invalid because stops are missing, they
    see an error they
    cometio."""
    st = state_for(tmp_path)
    for tipo in ("solid", "gradient", "procedural", "sequence", "image"):
        errores = st.set_background_type(tipo)
        assert errores == [], f"{tipo}: {errores}"
        assert st.raw["background"]["type"] == tipo


def test_switching_to_procedural_keeps_the_existing_stops(tmp_path):
    """The gradient the user already tuned is not lost when animating it: that is
    precisely the point of procedural starting from the gradient."""
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
    """Fewer than two stops is not a gradient: the validator rejects it, so deleting
    the second-to-last has to refuse rather than leave the profile broken."""
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
    """The UI draws the fields the chosen type accepts; if it invented them, it
    would write keys the validator rejects."""
    st = state_for(tmp_path)
    assert set(st.background_fields("solid")) == {"color"}
    assert "stops" not in st.background_fields("solid")
    assert set(st.background_fields("procedural")) >= {"name", "speed", "period",
                                                       "angle"}
    assert set(st.background_fields("sequence")) >= {"src", "fps", "fit"}
    assert "speed" not in st.background_fields("sequence")


# --- boxes and hit testing, for dragging on the preview ---

def test_every_widget_has_a_bounding_box(tmp_path):
    st = state_for(tmp_path)
    for wid in st.widget_ids():
        caja = st.widget_bbox(wid)
        assert caja is not None, wid
        x0, y0, x1, y1 = caja
        assert x1 > x0 and y1 > y0, (wid, caja)


def test_a_text_box_follows_the_rendered_text(tmp_path):
    """A text's box comes from measuring the font with the demo value, not from an
    invented radius: a 74 px clock and a 14 px label cannot have the same hit
    area."""
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
    """The list order is the paint order: the top one is what the user sees and what
    they expect to grab."""
    st = state_for(tmp_path)
    st.add_widget("rect", "tapa")
    st.set_field("tapa", "x", "24")
    st.set_field("tapa", "y", "316")
    st.set_field("tapa", "w", "272")
    st.set_field("tapa", "h", "16")
    assert st.hit_test(100, 320) == "tapa"      # it covers cpu-bar, which comes before


def test_dragging_moves_the_widget_to_the_point(tmp_path):
    """Dragging moves by DELTA, it does not put the corner under the cursor:
    otherwise the widget jumps when grabbed anywhere other than that corner."""
    st = state_for(tmp_path)
    x0, y0 = st.widget("cpu-load")["x"], st.widget("cpu-load")["y"]
    st.begin_drag("cpu-load", x0 + 10, y0 + 5)
    st.drag_to(x0 + 40, y0 + 25)
    assert st.widget("cpu-load")["x"] == x0 + 30
    assert st.widget("cpu-load")["y"] == y0 + 20
    assert st.dirty is True


def test_dragging_clamps_to_the_canvas(tmp_path):
    """A widget dragged off the canvas disappears from the panel and there is no
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
    """Deleting an alias some widget uses leaves the layout invalid: the validator
    rejects the profile and the panel keeps the previous one."""
    st = state_for(tmp_path)
    errores = st.remove_font("hero")
    assert errores and any("hero" in e for e in errores)
    assert "hero" in st.raw["fonts"]


def test_an_unused_font_can_be_removed(tmp_path):
    st = state_for(tmp_path)
    st.add_font("unused")
    assert st.remove_font("unused") == []
    assert "unused" not in st.raw["fonts"]


def test_the_available_families_are_offered(tmp_path):
    """The family combo is filled from the installed fonts: typing the name by hand
    is how a family that does not exist gets written and the widget ends up with the
    default font with no warning."""
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
    """Dragging fires one change per mouse pixel. If each were a step,
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
    """An unbounded history stores one copy of the layout per pixel of drag: that is
    300 KB a copy with 154 widgets."""
    st = state_for(tmp_path)
    for i in range(200):
        st.set_field("cpu-load", "x", str(20 + i % 50))
    assert len(st._historial) <= st.MAX_UNDO


# --- reglas de color ---

def test_rules_are_listed_parsed(tmp_path):
    """The JSON stores the rule as "> 90"; the UI needs the operator and the number
    separately in order to offer a combo and a field."""
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
    # and in the JSON it ended up as the comparison the validator expects
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


# --- pistas por tipo de fondo ---


def test_the_video_hint_says_ffmpeg_is_needed(monkeypatch):
    """The hint is the only place the user learns that video depends on an external
    executable. If it is missing, it has to say HOW to get it: a warning reading
    "needs ffmpeg" and nothing else leaves them where they were."""
    from vmaxpanel import editor
    from vmaxpanel.render import video
    monkeypatch.setattr(video, "buscar_ffmpeg", lambda: None)
    pista = editor.pista_fondo("video")
    assert "ffmpeg" in pista
    assert "winget" in pista


def test_the_video_hint_confirms_ffmpeg_when_it_is_there(monkeypatch):
    from vmaxpanel import editor
    from vmaxpanel.render import video
    monkeypatch.setattr(video, "buscar_ffmpeg", lambda: r"C:\bin\ffmpeg.exe")
    pista = editor.pista_fondo("video")
    assert "ffmpeg" in pista
    assert "winget" not in pista


def test_the_sequence_hint_explains_that_src_is_a_folder():
    """It was written but unreachable: the guard above caught 'sequence' along with
    'procedural' and returned early, so the user never saw the one line explaining
    that src is a folder."""
    from vmaxpanel import editor
    assert "folder" in editor.pista_fondo("sequence")


def test_a_static_background_has_no_hint():
    from vmaxpanel import editor
    assert editor.pista_fondo("solid") == ""
    assert editor.pista_fondo("gradient") == ""



# --- the widget tree: sections with friendly names ---


def test_the_widget_tree_groups_widgets_by_what_they_measure(tmp_path):
    """A flat list of 47 ids makes you remember what each one is. Grouped by
    CPU/GPU/RAM, finding the one you want is reading, not recalling."""
    st = state_for(tmp_path)
    arbol = st.widget_tree()
    grupos = [g for g, _ in arbol]
    assert "CPU" in grupos and "GPU" in grupos
    cpu = dict(arbol)["CPU"]
    assert any(wid == "cpu-load" for wid, _ in cpu), cpu


def test_a_row_shows_the_friendly_name_and_the_id(tmp_path):
    """The id alone ("mem-load") says nothing to somebody who did not write the
    profile; the label alone cannot be matched against the JSON. Both."""
    st = state_for(tmp_path)
    fila = next(etiqueta for g, filas in st.widget_tree()
                for wid, etiqueta in filas if wid == "cpu-load")
    assert "(cpu-load)" in fila
    assert fila.split(" (")[0].strip(), "the row has no friendly name"
    assert fila != "cpu-load"


def test_widgets_without_a_metric_land_in_their_own_group(tmp_path):
    """A label and a rect measure nothing, so no metric group is theirs. Left
    ungrouped they would be scattered through the tree with no reason."""
    st = state_for(tmp_path)
    grupos = dict(st.widget_tree())
    assert st.DECORATION in grupos
    ids = [wid for wid, _ in grupos[st.DECORATION]]
    assert any(st.widget(wid)["type"] in ("label", "rect") for wid in ids)


def test_a_label_row_shows_the_text_it_draws(tmp_path):
    """A label has no metric to name it, but it does have the text on screen --
    which is how the user recognises it on the panel."""
    st = state_for(tmp_path)
    st.add_widget("label", "rotulo-sistema")
    st.set_field("rotulo-sistema", "text", "SYSTEM")
    fila = next(etiqueta for g, filas in st.widget_tree()
                for wid, etiqueta in filas if wid == "rotulo-sistema")
    assert "SYSTEM" in fila


def test_the_tree_keeps_the_profile_order_inside_each_group(tmp_path):
    """The order of the widgets list is the paint order. Grouping already gives
    up the global view of it; scrambling it inside a group as well would leave
    no way to reason about what covers what."""
    st = state_for(tmp_path)
    orden = st.widget_ids()
    for _, filas in st.widget_tree():
        ids = [wid for wid, _ in filas]
        assert ids == sorted(ids, key=orden.index), ids


def test_every_widget_appears_exactly_once(tmp_path):
    """A widget missing from the tree cannot be selected at all -- it would be
    invisible to the editor while still being drawn on the panel."""
    st = state_for(tmp_path)
    en_arbol = [wid for _, filas in st.widget_tree() for wid, _ in filas]
    assert sorted(en_arbol) == sorted(st.widget_ids())


# --- the preview's zoom and grid ---
#
# Pure arithmetic on purpose, the same reason theme.py keeps its palette out of Tk:
# what goes wrong here is the formula, not the widget, and a formula can be checked
# without opening a window.


def test_the_wheel_zooms_in_and_out():
    from vmaxpanel import editor
    assert editor.zoom_step(1.0, hacia_arriba=True) > 1.0
    assert editor.zoom_step(1.0, hacia_arriba=False) < 1.0


def test_the_zoom_stops_at_its_limits():
    """Without a ceiling a few flicks of the wheel ask Pillow to scale a 320x1480
    frame to something enormous and the editor stops answering."""
    from vmaxpanel import editor
    k = 1.0
    for _ in range(100):
        k = editor.zoom_step(k, hacia_arriba=True)
    assert k == editor.ZOOM_MAX
    for _ in range(200):
        k = editor.zoom_step(k, hacia_arriba=False)
    assert k == editor.ZOOM_MIN


def test_zooming_keeps_the_point_under_the_cursor_still():
    """Zoom that ignores the pointer walks away from whatever you were looking at:
    you aim at a widget, zoom, and it is off screen.

    A panel point at 200 px, drawn at scale 2 with the pointer 50 px into the
    viewport, has to sit 400-50 = 350 px from the document's origin.
    """
    from vmaxpanel import editor
    f = editor.vista_tras_zoom(200, 2.0, puntero=50, total=1000)
    assert f == pytest.approx(0.35)


def test_the_view_never_leaves_the_document():
    """A fraction outside 0..1 is what xview_moveto refuses, and the preview stays
    frozen at the edge with no error anywhere."""
    from vmaxpanel import editor
    assert editor.vista_tras_zoom(0, 1.0, puntero=500, total=1000) == 0.0
    assert editor.vista_tras_zoom(5000, 1.0, puntero=0, total=1000) == 1.0
    # No document to scroll: no division by zero either.
    assert editor.vista_tras_zoom(10, 1.0, puntero=0, total=0) == 0.0


def test_the_grid_lines_cover_the_whole_panel():
    from vmaxpanel import editor
    verticales, horizontales = editor.lineas_grilla(320, 1480, 20)
    assert verticales[0] == 0 and horizontales[0] == 0
    assert verticales[-1] == 320                    # reaches the far edge
    assert horizontales[-1] == 1480
    assert verticales[1] - verticales[0] == 20


def test_a_grid_with_no_spacing_draws_nothing():
    """range() with step 0 raises, and it would do it inside a redraw -- where
    Tkinter swallows the exception and the preview simply stops updating."""
    from vmaxpanel import editor
    assert editor.lineas_grilla(320, 1480, 0) == ([], [])
    assert editor.lineas_grilla(320, 1480, -5) == ([], [])
