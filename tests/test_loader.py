import json
import os
from pathlib import Path

import pytest

from vmaxpanel.layout import loader, model
from tests.test_schema import MINIMAL


def write(tmp_path, obj, name="p.json"):
    path = tmp_path / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_loads_valid_layout():
    lay = loader.loads(json.dumps(MINIMAL))
    assert isinstance(lay, model.Layout)
    assert lay.name == "Test"


def test_loads_invalid_json_raises_with_errors():
    with pytest.raises(loader.LayoutError) as e:
        loader.loads("{no es json")
    assert e.value.errors


def test_loads_invalid_layout_lists_every_error():
    bad = dict(MINIMAL, version=99, background={"type": "plasma"})
    with pytest.raises(loader.LayoutError) as e:
        loader.loads(json.dumps(bad))
    assert len(e.value.errors) >= 2


def test_save_then_load_roundtrips(tmp_path):
    path = tmp_path / "out.json"
    loader.save(loader.loads(json.dumps(MINIMAL)), path)
    again = loader.load(path)
    assert again.name == "Test"
    assert again.widgets[1].rules[0].value == 85.0
    assert again.panel.rotate == 180


def test_store_keeps_previous_layout_when_reload_fails(tmp_path):
    path = write(tmp_path, MINIMAL)
    store = loader.ProfileStore(path)
    assert store.load_now() == []
    good = store.current
    assert good.name == "Test"

    path.write_text("{roto", encoding="utf-8")
    changed, errors = store.reload_if_changed()
    assert changed is False
    assert errors
    assert store.current is good        # el panel sigue mostrando el layout bueno


def test_store_reloads_when_the_file_changes(tmp_path):
    path = write(tmp_path, MINIMAL)
    store = loader.ProfileStore(path)
    store.load_now()
    write(tmp_path, dict(MINIMAL, name="Otro"), name="p.json")
    changed, errors = store.reload_if_changed()
    assert changed is True and errors == []
    assert store.current.name == "Otro"


def test_store_reports_no_change_when_untouched(tmp_path):
    store = loader.ProfileStore(write(tmp_path, MINIMAL))
    store.load_now()
    assert store.reload_if_changed() == (False, [])


def test_store_on_missing_file_reports_error_without_raising(tmp_path):
    store = loader.ProfileStore(tmp_path / "no-existe.json")
    errors = store.load_now()
    assert errors and store.current is None


def test_rule_thresholds_roundtrip_across_magnitudes(tmp_path):
    # :g (la version original) pasa a notacion cientifica desde 1e6, que
    # schema._RULE_RE no reconoce. Cubre grande (>=1e6), fraccionario chico,
    # negativo y el caso ordinario ya cubierto en otro test.
    raw = json.loads(json.dumps(MINIMAL))
    raw["widgets"][1]["rules"] = [
        {"when": "> 1234567", "color": "#FF4444"},
        {"when": "< 0.0001", "color": "#4444FF"},
        {"when": ">= -85.5", "color": "#44FF44"},
        {"when": "<= 85", "color": "#FFFFFF"},
    ]
    path = tmp_path / "rules.json"
    loader.save(loader.loads(json.dumps(raw)), path)
    again = loader.load(path)
    assert [r.value for r in again.widgets[1].rules] == [1234567.0, 0.0001, -85.5, 85.0]


def test_store_recovers_after_user_fixes_the_file(tmp_path):
    # El caso de uso real de hot-reload: el usuario tipea mal, el panel se
    # queda con el layout bueno, el usuario corrige el typo y el panel
    # levanta el archivo corregido en la siguiente pasada de polling.
    path = write(tmp_path, MINIMAL)
    store = loader.ProfileStore(path)
    store.load_now()
    good = store.current

    path.write_text("{roto", encoding="utf-8")
    changed, errors = store.reload_if_changed()
    assert changed is False and errors
    assert store.current is good

    write(tmp_path, MINIMAL, name="p.json")
    changed, errors = store.reload_if_changed()
    assert changed is True and errors == []
    assert store.current.name == "Test"


def test_the_shipped_profile_survives_a_save_load_roundtrip(tmp_path):
    """El editor de fase 3 guarda con loader.save, asi que un layout que
    save() emite y load() rechaza es un archivo que el usuario no puede
    volver a abrir. MINIMAL no cubre esto: no tiene los campos opcionales
    que default a None."""
    lay = loader.load(Path("vmaxpanel/profiles/vitals.json"))
    path = tmp_path / "roundtrip.json"
    loader.save(lay, path)
    again = loader.load(path)
    assert [w.id for w in again.widgets] == [w.id for w in lay.widgets]


def test_optional_fields_that_default_to_none_are_not_emitted(tmp_path):
    """Un rect con fill y sin stroke serializaba "stroke": null, y null no
    es un color valido. Mismo problema con min/max de bar/arc/graph."""
    raw = json.loads(json.dumps(MINIMAL))
    raw["widgets"].append({"id": "sep", "type": "rect", "x": 24, "y": 164,
                           "w": 272, "h": 1, "fill": "#242834"})
    path = tmp_path / "rect.json"
    loader.save(loader.loads(json.dumps(raw)), path)
    written = json.loads(path.read_text(encoding="utf-8"))
    sep = [w for w in written["widgets"] if w["id"] == "sep"][0]
    assert "stroke" not in sep
    assert [w for w in written["widgets"] if w["id"] == "bar"][0].get("min", "ausente") == "ausente"
    assert loader.load(path).widgets[-1].fill == "#242834"


def test_reload_detects_a_change_that_lands_in_the_same_filesystem_tick(tmp_path):
    """El criterio era solo st_mtime_ns. Dos escrituras en el mismo tick del
    filesystem dejaban el mtime igual y la segunda se perdia: el editor de
    fase 3 guarda dos veces seguidas y el panel se queda con la primera. Es
    el flake de test_store_recovers_after_user_fixes_the_file y tambien un
    agujero real del hot-reload."""
    import os
    path = write(tmp_path, MINIMAL)
    store = loader.ProfileStore(path)
    assert store.load_now() == []
    before = os.stat(path)

    raw = json.loads(json.dumps(MINIMAL))
    raw["name"] = "Editado"
    path.write_text(json.dumps(raw), encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))   # mismo tick

    changed, errors = store.reload_if_changed()
    assert changed is True and errors == []
    assert store.current.name == "Editado"


def test_reload_reports_no_change_when_the_file_is_untouched(tmp_path):
    path = write(tmp_path, MINIMAL)
    store = loader.ProfileStore(path)
    store.load_now()
    assert store.reload_if_changed() == (False, [])
    assert store.reload_if_changed() == (False, [])


def test_an_animated_background_survives_the_roundtrip(tmp_path):
    """El editor de fase 3 guarda con loader.save: un fondo animado que se
    escribe y no se puede volver a abrir es trabajo perdido."""
    raw = json.loads(json.dumps(MINIMAL))
    raw["background"] = {"type": "procedural", "name": "pulse", "period": 8,
                         "stops": [{"at": 0.0, "color": "#101725"},
                                   {"at": 1.0, "color": "#141A26"}]}
    path = tmp_path / "animado.json"
    loader.save(loader.loads(json.dumps(raw)), path)
    otro = loader.load(path)
    assert otro.background.type == "procedural"
    assert otro.background.name == "pulse"
    assert otro.background.period == 8.0


def test_two_writers_do_not_share_the_temp_file(tmp_path, monkeypatch):
    """El temporal tenia nombre fijo (`<perfil>.tmp`), asi que dos escritores
    en paralelo -- la bandeja cambiando el fps y el editor guardando -- se
    pisaban el temporal y uno de los dos podia escribir un archivo mezclado.
    Ahora cada escritura usa su propio temporal."""
    path = tmp_path / "p.json"
    path.write_text(json.dumps(MINIMAL), encoding="utf-8")
    vistos = []
    real = os.replace

    def espiar(src, dst):
        vistos.append(str(src))
        return real(src, dst)

    monkeypatch.setattr(loader.os, "replace", espiar)
    loader.save_raw(json.loads(json.dumps(MINIMAL)), path)
    loader.save_raw(json.loads(json.dumps(MINIMAL)), path)
    assert len(set(vistos)) == 2, f"reuso el mismo temporal: {vistos}"
    assert not list(tmp_path.glob("*.tmp*")), "quedo un temporal sin borrar"


def test_every_widget_type_survives_a_roundtrip(tmp_path):
    """arc, graph e image no los round-trippeaba ningun test: el reviewer los
    verifico a mano. Un tipo que save() escribe y load() rechaza es trabajo
    perdido, y ya paso dos veces (rect con stroke null, y el fondo procedural)."""
    raw = json.loads(json.dumps(MINIMAL))
    raw["widgets"] += [
        {"id": "a", "type": "arc", "metric": "cpu.load", "x": 100, "y": 100,
         "r": 40, "thickness": 6, "start_angle": 135, "sweep": 270,
         "fill": "#3987E5", "track": "#242834"},
        {"id": "g", "type": "graph", "metric": "cpu.load", "x": 10, "y": 10,
         "w": 200, "h": 60, "color": "#3987E5", "samples": 90, "min": 0, "max": 100},
        {"id": "r", "type": "rect", "x": 5, "y": 5, "w": 100, "h": 1,
         "fill": "#242834"},
        {"id": "r2", "type": "rect", "x": 5, "y": 9, "w": 100, "h": 40,
         "radius": 6, "stroke": "#FFFFFF", "stroke_width": 2},
    ]
    path = tmp_path / "todos.json"
    loader.save(loader.loads(json.dumps(raw)), path)
    otro = loader.load(path)
    por_id = {w.id: w for w in otro.widgets}
    assert por_id["a"].sweep == 270.0 and por_id["a"].r == 40
    assert por_id["g"].samples == 90 and por_id["g"].max == 100.0
    assert por_id["r"].h == 1 and por_id["r"].stroke is None
    assert por_id["r2"].stroke == "#FFFFFF" and por_id["r2"].stroke_width == 2


def test_every_background_type_survives_a_roundtrip(tmp_path):
    fondos = [
        {"type": "solid", "color": "#101010"},
        {"type": "gradient", "angle": 45,
         "stops": [{"at": 0.0, "color": "#101725"}, {"at": 1.0, "color": "#141A26"}]},
        {"type": "image", "src": "fondo.png", "fit": "contain", "color": "#000000"},
        {"type": "sequence", "src": "cuadros", "fps": 12, "fit": "cover"},
        {"type": "procedural", "name": "scroll", "speed": 30, "angle": 90,
         "stops": [{"at": 0.0, "color": "#101725"}, {"at": 1.0, "color": "#141A26"}]},
    ]
    for i, fondo in enumerate(fondos):
        raw = json.loads(json.dumps(MINIMAL))
        raw["background"] = fondo
        path = tmp_path / f"bg{i}.json"
        loader.save(loader.loads(json.dumps(raw)), path)
        otro = loader.load(path)
        assert otro.background.type == fondo["type"], fondo["type"]
