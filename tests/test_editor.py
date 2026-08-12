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
