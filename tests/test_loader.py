import json

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
