import copy
import json
import os
from pathlib import Path

import pytest

from vmaxpanel.layout import loader, model, schema
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
    assert store.current is good        # the panel keeps showing the good layout


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
    # :g (the original version) goes scientific from 1e6, which
    # schema._RULE_RE no reconoce. Cubre grande (>=1e6), fraccionario chico,
    # negative, and the ordinary case already covered by another test.
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
    # The real hot-reload use case: the user mistypes, the panel keeps the good
    # layout, the user fixes the typo and the panel picks the corrected file up on
    # the next polling pass.
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
    """The editor saves through loader.save, so a layout that save() emits and
    load() rejects is a file the user cannot reopen. MINIMAL does not cover this: it
    has none of the optional fields that default to None."""
    lay = loader.load(Path("vmaxpanel/profiles/vitals.json"))
    path = tmp_path / "roundtrip.json"
    loader.save(lay, path)
    again = loader.load(path)
    assert [w.id for w in again.widgets] == [w.id for w in lay.widgets]


def test_optional_fields_that_default_to_none_are_not_emitted(tmp_path):
    """A rect with fill and no stroke serialised "stroke": null, and null is not a
    valid colour. Same problem with min/max on bar/arc/graph."""
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
    """The rule used to be st_mtime_ns alone. Two writes in the same filesystem tick
    left the mtime unchanged and the second was lost: the editor saves twice in a
    row and the panel keeps the first. It is the flake in
    test_store_recovers_after_user_fixes_the_file and also a real hot-reload hole."""
    import os
    path = write(tmp_path, MINIMAL)
    store = loader.ProfileStore(path)
    assert store.load_now() == []
    before = os.stat(path)

    raw = json.loads(json.dumps(MINIMAL))
    raw["name"] = "Editado"
    path.write_text(json.dumps(raw), encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))   # the same tick

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
    """The editor saves through loader.save: an animated background that is written
    and cannot be reopened is lost work."""
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
    """The temp file used to have a fixed name (`<profile>.tmp`), so two writers in
    parallel -- the tray changing the fps and the editor saving -- clobbered each
    other's temp file and one of them could write a mixed file. Now each write uses
    its own temp file."""
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
    assert len(set(vistos)) == 2, f"it reused the same temp file: {vistos}"
    assert not list(tmp_path.glob("*.tmp*")), "a temp file was left behind"


def test_every_widget_type_survives_a_roundtrip(tmp_path):
    """arc, graph and image were round-tripped by no test: they were checked by
    hand. A type that save() writes and load() rejects is lost work, and it has
    already happened twice (a rect with a null stroke, and the procedural
    background)."""
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


def test_font_fallbacks_survive_a_save(tmp_path):
    """The editor saves through dumps_layout: if the chain is not serialised, saving
    from the editor deletes it silently and the profile stops being portable without
    anybody
    note."""
    raw = copy.deepcopy(MINIMAL)
    raw["fonts"]["mono-14"]["fallbacks"] = ["Cascadia Mono", "Courier New"]
    p = tmp_path / "p.json"
    loader.save_raw(raw, p)
    vuelta = json.loads(p.read_text(encoding="utf-8"))
    assert vuelta["fonts"]["mono-14"]["fallbacks"] == ["Cascadia Mono", "Courier New"]
    assert schema.validate(vuelta) == []


def test_to_dict_keeps_the_fallback_chain(tmp_path):
    """to_dict goes from the MODEL to a dict, which is a different path from
    save_raw: if it is lost here, any tool serialising from the model deletes the
    chain."""
    raw = copy.deepcopy(MINIMAL)
    raw["fonts"]["mono-14"]["fallbacks"] = ["Courier New"]
    d = loader.to_dict(schema.build(raw))
    assert d["fonts"]["mono-14"]["fallbacks"] == ["Courier New"]
    assert "fallbacks" not in d["fonts"]["mono-bold-60"]     # no chain, no key
    assert schema.validate(d) == []


# --- round-trip of every type, not of a sample ---


def _con(widgets=None, background=None):
    raw = copy.deepcopy(MINIMAL)
    if widgets is not None:
        raw["widgets"] = widgets
    if background is not None:
        raw["background"] = background
    return raw


def test_every_widget_type_survives_a_round_trip(tmp_path):
    """arc, graph and image were round-tripped by no test: they were checked BY HAND.
    A field lost on save (it happened: _is_default discarded a text's colour) is
    noticed by nobody until the
    perfil vuelve distinto."""
    widgets = [
        {"id": "a", "type": "arc", "metric": "cpu.load", "x": 40, "y": 40, "r": 30,
         "start_angle": 135.0, "sweep": 270.0, "fill": "#FF4D00",
         "track": "#241812", "thickness": 8, "min": 0.0, "max": 100.0},
        {"id": "g", "type": "graph", "metric": "cpu.load", "x": 10, "y": 100,
         "w": 200, "h": 40, "color": "#FF4D00", "track": "#241812", "samples": 60,
         "min": 0.0, "max": 100.0},
        {"id": "i", "type": "image", "src": "logo.png", "x": 5, "y": 5,
         "w": 32, "h": 32},
        {"id": "r", "type": "rect", "x": 0, "y": 200, "w": 100, "h": 2,
         "fill": "#3A2418"},
    ]
    raw = _con(widgets=widgets)
    assert schema.validate(raw) == [], schema.validate(raw)
    p = tmp_path / "p.json"
    loader.save_raw(raw, p)
    vuelta = json.loads(p.read_text(encoding="utf-8"))
    assert schema.validate(vuelta) == []
    for original in widgets:
        guardado = next(w for w in vuelta["widgets"] if w["id"] == original["id"])
        for k, v in original.items():
            assert guardado[k] == v, f"{original['id']}.{k}: {guardado.get(k)!r} != {v!r}"


def test_every_background_type_survives_a_round_trip(tmp_path):
    fondos = [
        {"type": "solid", "color": "#0A0705"},
        {"type": "gradient", "angle": 90,
         "stops": [{"at": 0.0, "color": "#000000"}, {"at": 1.0, "color": "#FFFFFF"}]},
        {"type": "image", "src": "back.png", "fit": "cover", "color": "#000000"},
        {"type": "sequence", "src": "cuadros", "fit": "cover", "fps": 12.5,
         "color": "#000000"},
        {"type": "video", "src": "loop.mp4", "fit": "contain", "fps": 30,
         "color": "#07080B"},
        {"type": "procedural", "name": "scroll", "speed": 150, "angle": 90,
         "stops": [{"at": 0.0, "color": "#0A0705"}, {"at": 1.0, "color": "#6B2408"}]},
    ]
    for fondo in fondos:
        raw = _con(background=fondo)
        assert schema.validate(raw) == [], f"{fondo['type']}: {schema.validate(raw)}"
        p = tmp_path / f"{fondo['type']}.json"
        loader.save_raw(raw, p)
        vuelta = json.loads(p.read_text(encoding="utf-8"))
        assert vuelta["background"] == fondo, fondo["type"]

        # Through the model -- the other serialisation path -- the invariant is to
        # LOSE nothing and stay valid, not "add nothing": to_dict comes from the
        # model, which has defaults (period=6.0), and cannot tell "absent" from
        # "igual al default". Perder un campo es un bug; emitir un default explicito
        # is noise, and the JSON the user opens is written by save_raw, not by this
        # path.
        del_modelo = loader.to_dict(schema.build(raw))["background"]
        for k, v in fondo.items():
            assert del_modelo[k] == v, f"{fondo['type']}.{k} se perdio o cambio"
        assert schema.validate(_con(background=del_modelo)) == [], fondo["type"]
