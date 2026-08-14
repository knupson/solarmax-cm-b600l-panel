"""Tests for the editor window.

The UI is glue, but glue breaks too: selecting a widget in the list did not update
the properties panel because the callback's method did not exist, and Tkinter
swallows the callback AttributeError and prints to stderr -- which under pythonw
goes nowhere. These tests build the real window and exercise the callbacks.
"""
import json

import pytest

from vmaxpanel.editor import EditorState

PROFILE = "vmaxpanel/profiles/vitals.json"


ORIGINAL = open(PROFILE, encoding="utf-8").read()


@pytest.fixture(scope="module")
def _ventana(tmp_path_factory):
    """ONE single Tk root for the whole module.

    Creating and destroying a Tk() per test made two of seven fail with "Tcl wasn't
    installed properly" -- which is what Tkinter says when roots are abused, not an
    installation problem. Worse still: with the defensive skip, the failing tests
    were SKIPPED, so the coverage was lost silently. One root plus state restored
    between tests gives isolation without changing how the window is built in
    production.
    """
    pytest.importorskip("tkinter")
    from vmaxpanel.editor import EditorWindow

    path = tmp_path_factory.mktemp("editor") / "editando.json"
    path.write_text(ORIGINAL, encoding="utf-8")
    w = EditorWindow(EditorState(path))
    # INVISIBLE, but mapped. Running the suite on a machine somebody is using
    # covered their screen with the editor window, once per run. `withdraw()` is no
    # good: it unmaps the window and winfo_width()/geometry() start returning 1,
    # which is exactly what several of these tests measure. Alpha 0 leaves it mapped
    # -- the measurements stay real -- and nothing is visible. `-topmost` explicitly
    # False in case some window manager promotes it.
    try:
        w.root.wm_attributes("-alpha", 0.0)
        w.root.wm_attributes("-topmost", False)
    except Exception:
        pass                      # on a Tk without alpha support it shows; not fatal
    w.root.update()
    yield w
    try:
        w.root.destroy()
    except Exception:
        pass


@pytest.fixture
def ventana(_ventana):
    """Returns the window with the profile as freshly opened.

    Besides the profile, the two things about the WINDOW that one test can leave
    changed and another can read are reset: its size and where the focus is. Sharing
    a single Tk root is necessary (see _ventana) but it turns any global state into
    an ordering dependency -- with pytest-randomly, into failures that appear and
    disappear depending on the seed. Resetting here is cheaper than chasing them one
    by one.
    """
    _ventana.state.path.write_text(ORIGINAL, encoding="utf-8")
    _ventana._discard()
    _ventana.root.geometry("1200x900")
    _ventana.lista.focus_set()
    _ventana.root.update()
    return _ventana


def metrica_mostrada(w):
    """The metric id the selector is showing right now.

    The combo shows the friendly label, not the id: the reverse mapping is what the
    window uses to write into the state."""
    combo = w._pickers.get("metric")
    if combo is None:
        return None
    return w._metric_por_etiqueta.get(combo.get())


def seleccionar(w, wid):
    """Selects a widget the way the window itself does.

    Through `_seleccionar_en_arbol` and not `Treeview.selection_set` directly: a
    row inside a folded group cannot be selected by a click either, so opening
    its group is part of what selecting means here.
    """
    w._seleccionar_en_arbol(wid)
    w.lista.event_generate("<<TreeviewSelect>>")
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
    assert "format" not in ventana._fields          # a bar has no format
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
    """Tkinter prints callback exceptions to stderr and carries on. Under pythonw
    that is an invisible failure: the window stops responding and nothing says so."""
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
    """The user's path, with a real click instead of a
    synthetic <<TreeviewSelect>>.

    The difference matters: event_generate of a virtual event is NOT dispatched if
    the window is not mapped yet, so a test using only the synthetic event passes or
    not depending on when update() was called -- and a manual check with that method
    made it look as though the fix did not work when it did.
    """
    ventana.lista.selection_remove(*ventana.lista.selection())
    ventana.root.update()

    # The group has to be open for the row to have a box at all -- a folded row
    # is not merely off-screen, it does not exist as a visible item.
    ventana.lista.item(ventana.lista.parent("cpu-load"), open=True)
    ventana.lista.see("cpu-load")
    ventana.root.update()
    caja = ventana.lista.bbox("cpu-load")
    assert caja is not None, "the row is not visible; see() was not enough"
    x, y = caja[0] + 5, caja[1] + 2
    ventana.lista.event_generate("<Button-1>", x=x, y=y)
    ventana.lista.event_generate("<ButtonRelease-1>", x=x, y=y)
    ventana.root.update()

    assert ventana._selected() == "cpu-load"
    assert metrica_mostrada(ventana) == "cpu.load"


def test_arrows_move_the_widget_only_when_not_editing_a_field(ventana):
    """The arrows are bound to the window so a widget can be nudged 1 px at a time.
    But with the focus in a text field they have to move the cursor and NOTHING
    ELSE: typing "182" into x and having the left arrow shift the widget while you
    correct a digit is silent layout corruption."""
    seleccionar(ventana, "cpu-load")
    x0 = ventana.state.widget("cpu-load")["x"]

    campos = [c for c in ventana.props.winfo_children()
              if c.winfo_class() in ("TEntry", "Entry")]
    campos[0].focus_set()
    ventana.root.update()
    # `_flechas` decides with root.focus_get(), and that depends on SYSTEM focus: if
    # the window does not have it -- another application in front, a remote session,
    # a runner with no desktop -- focus_set() has no effect and focus_get() does not
    # return what was just asked for. The test would then be measuring the window
    # manager's mood rather than the filter: it failed in the full suite and passed
    # when run alone. It says why it skips instead of forcing the focus, which would
    # steal the screen from whoever is using the machine.
    if ventana.root.focus_get() is not campos[0]:
        pytest.skip("the window does not have system focus: the focus filter "
                    "cannot be observed")
    campos[0].event_generate("<Left>")
    ventana.root.update()
    assert ventana.state.widget("cpu-load")["x"] == x0, \
        "the arrow moved the widget while a field was being edited"

    ventana.lista.focus_set()
    ventana.root.update()
    ventana.lista.event_generate("<Left>")
    ventana.root.update()
    assert ventana.state.widget("cpu-load")["x"] == x0 - 1


def test_the_preview_grows_with_the_window(ventana):
    """A fixed scale wastes a maximised window: the panel is 320x1480 and at 0.36 it
    looks like a thumbnail. The preview has to use the
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
    """Changing the image changes the Label size, which fires another <Configure>:
    without a guard that is an infinite redraw loop."""
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
    """The `metric` field used to be free text: you had to remember that
    "vol.D.free" exists. Now it is a list with friendly names grouped
    por dispositivo."""
    seleccionar(ventana, "cpu-load")
    combo = ventana._pickers.get("metric")
    assert combo is not None, "metric sigue siendo un campo de texto"
    valores = list(combo.cget("values"))
    assert valores, "the selector came out empty"
    # the value shown is the friendly label, not the id
    assert combo.get() != "cpu.load"
    assert "CPU" in combo.get() or "carga" in combo.get().lower()
    # and the groups appear as non-selectable headings
    assert any(v.startswith("——") for v in valores), "there are no group headings"


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
    """Picking a heading must not write "—— CPU ——" as the metric."""
    seleccionar(ventana, "cpu-load")
    antes = ventana.state.widget("cpu-load")["metric"]
    combo = ventana._pickers["metric"]
    combo.set(next(v for v in combo.cget("values") if v.startswith("——")))
    ventana._on_pick_metric()
    assert ventana.state.widget("cpu-load")["metric"] == antes
    assert ventana.state.errors == []


def test_the_window_has_tabs_for_widgets_background_and_panel(ventana):
    """The background, the fonts and the panel are not widgets: putting them in the
    same column would force a choice between seeing the list and seeing the
    background."""
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
    """The preview is a single fixed frame: if the background moves, the user has to
    know that what they see is not an animation stopped by a bug."""
    ventana._bg_type.set("procedural")
    ventana._on_pick_bg_type()
    ventana.root.update()
    assert "animate" in ventana._bg_hint.cget("text").lower()


def test_clicking_the_preview_selects_that_widget(ventana):
    """Clicking on the drawn panel is the natural way to select: a list of 47 names
    requires remembering what everything is called."""
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
    """A click on empty space must not deselect: the properties panel would empty
    out and the user would lose what they were editing."""
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
    assert len(valores) > 5, "the families combo came out nearly empty"
    assert any("consol" in v.lower() for v in valores)


def test_control_z_undoes_from_the_window(ventana):
    seleccionar(ventana, "cpu-load")
    x0 = ventana.state.widget("cpu-load")["x"]
    ventana._move(20, 0)
    assert ventana.state.widget("cpu-load")["x"] == x0 + 20
    # focus_force before the event_generate: a key goes to the focused widget, and in
    # an unfocused window Tk discards it without warning. Without this the test
    # passed or failed depending on what had focus at that instant -- and when it
    # failed, the diff read "it did not undo", which points at the code instead of at
    # the test. It is the same hole that already led to a wrong conclusion once, with
    # <<ListboxSelect>> on an unmapped window.
    ventana.root.focus_force()
    ventana.root.update()
    ventana.root.event_generate("<Control-z>", when="now")
    ventana.root.update()
    assert ventana.state.widget("cpu-load")["x"] == x0


def test_undo_refreshes_the_fields_and_the_preview(ventana):
    """Undoing without repainting leaves the fields showing the undone value: the
    user sees a number that is no longer the layout's."""
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
    """Only text widgets have colour rules in this engine: showing the section on a
    bar would promise something that does not exist."""
    seleccionar(ventana, "cpu-load")
    assert ventana._rule_rows, "a text with rules did not show the editor"
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


# --- exporting and importing from the editor ---


def test_exporting_from_the_editor_writes_a_bundle(ventana, tmp_path):
    destino = tmp_path / "salida.vmaxpanel"
    ventana._exportar_a(destino)
    assert destino.exists()
    assert "salida.vmaxpanel" in ventana.estado.cget("text")


def test_exporting_with_unsaved_changes_refuses_and_says_why(ventana, tmp_path):
    """Exporting reads the file from disk. With unsaved changes the bundle would
    carry the old version and the user would share something that is not what they
    see on screen -- the worst kind of error, because it is not noticed until
    somebody else
    abre."""
    seleccionar(ventana, "cpu-load")
    ventana._move(5, 0)
    destino = tmp_path / "should-not.vmaxpanel"
    ventana._exportar_a(destino)
    assert not destino.exists()
    assert "unsaved" in ventana.estado.cget("text").lower()


def test_exporting_over_an_existing_file_refuses(ventana, tmp_path):
    destino = tmp_path / "already-here.vmaxpanel"
    destino.write_bytes(b"something")
    ventana._exportar_a(destino)
    assert destino.read_bytes() == b"something"
    assert "already exists" in ventana.estado.cget("text")


def test_importing_from_the_editor_loads_the_imported_profile(ventana, tmp_path):
    """Importing and not opening the imported profile would leave the user guessing
    whether it worked. It imports and switches to editing it."""
    from vmaxpanel import bundle
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(ventana.state.path, zip_,
                          assets_dir=tmp_path / "assets-vacio")
    destino_p = tmp_path / "perfiles"
    ventana._importar_de(zip_, profiles_dir=destino_p, assets_dir=tmp_path / "a2")
    assert ventana.state.path.parent == destino_p
    assert ventana.state.raw["name"]
    assert "imported" in ventana.estado.cget("text").lower()


def test_a_bad_bundle_reports_in_the_status_bar_and_keeps_editing(ventana, tmp_path):
    falso = tmp_path / "x.vmaxpanel"
    falso.write_bytes(b"no soy un zip")
    antes = ventana.state.path
    ventana._importar_de(falso, profiles_dir=tmp_path / "p", assets_dir=tmp_path / "a")
    assert ventana.state.path == antes
    assert "not a readable bundle" in ventana.estado.cget("text")


# --- the action bar has to be on every tab ---


def test_the_save_button_is_not_trapped_inside_the_widgets_tab(ventana):
    """It used to be inside the Widgets tab, so while editing the background there
    was no save button and no status bar: the user changed the background, could not
    find where to apply it, restarted the engine and the change was lost. Reported
    verbatim: "there is no apply button and it does not save"."""
    assert not str(ventana._acciones).startswith(str(ventana.tabs) + ".")
    assert not str(ventana.estado).startswith(str(ventana.tabs) + ".")


def tipear(ventana, padre, var, valor):
    """Simulates typing into a field: the text stays in the control and is NOT
    applied -- that is the point, applying is what the user never did. The editor
    detects it with a trace on the variable, so changing it is exactly what happens
    when
    teclear."""
    entradas = [c for c in padre.winfo_children()
                if c.winfo_class() in ("TEntry", "Entry")]
    assert entradas, "that panel has no text fields"
    var.set(valor)
    ventana.root.update()


def test_changing_the_background_and_saving_persists_it(ventana):
    """The path that failed: typing into a background field and hitting Save,
    WITHOUT going through Enter. Entries only apply on <Return> or <FocusOut>, so
    the value just typed never reached the state and the old one was saved."""
    ventana.tabs.select(1)                       # the Background tab
    ventana._bg_type.set("solid")
    ventana._on_pick_bg_type()                   # the combo path, with its pruning
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
    """If what was typed stayed pending, it would reappear on the next save -- which
    is to say "Discard changes" would discard nothing."""
    seleccionar(ventana, "cpu-load")
    x0 = ventana.state.widget("cpu-load")["x"]
    tipear(ventana, ventana.props, ventana._fields["x"], "999")
    ventana._discard()
    ventana._save()
    en_disco = json.loads(ventana.state.path.read_text(encoding="utf-8"))
    assert [w for w in en_disco["widgets"]
            if w["id"] == "cpu-load"][0]["x"] == x0


def test_unsaved_changes_are_visible_in_the_title(ventana):
    """With no "there are unsaved changes" signal, restarting the engine from the
    tray looks like it is ignoring the edit -- when in fact the edit never reached
    the disk."""
    assert "unsaved" not in ventana.root.title()
    seleccionar(ventana, "cpu-load")
    ventana._move(3, 0)
    assert "unsaved" in ventana.root.title()
    ventana._save()
    assert "unsaved" not in ventana.root.title()


# --- choosing the background file ---


def test_choosing_an_asset_outside_the_project_copies_it_in(ventana, tmp_path):
    """`safe_asset_path` rejects anything outside vmaxpanel/assets, so choosing a
    video from the Desktop can only work by copying it in. Without this the user
    picks a file, the editor saves a path the engine rejects, and the background sits
    at a flat colour with no explanation."""
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
    dentro = assets / "fondos" / "already-here.mp4"
    dentro.write_bytes(b"x")

    nombre = ventana._usar_asset(dentro, assets_dir=assets)

    assert nombre == "fondos/already-here.mp4"     # relative, with / for the JSON
    assert list((assets / "fondos").iterdir()) == [dentro]


def test_a_name_collision_with_other_content_does_not_overwrite(ventana, tmp_path):
    """Copying over another profile's asset destroys somebody else's work over a
    nombre repetido."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "fondo.mp4").write_bytes(b"the one already there")
    otro = tmp_path / "another folder" / "fondo.mp4"
    otro.parent.mkdir()
    otro.write_bytes(b"the new one")

    nombre = ventana._usar_asset(otro, assets_dir=assets)

    assert nombre == "fondo-2.mp4"
    assert (assets / "fondo.mp4").read_bytes() == b"the one already there"
    assert (assets / "fondo-2.mp4").read_bytes() == b"the new one"


def test_choosing_the_same_file_twice_reuses_the_copy(ventana, tmp_path):
    """Same content and same name: it is the same file, another copy is not needed.
    Without this, pressing the button twice leaves fondo.mp4 and fondo-2.mp4
    identical."""
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
    """A `solid` has no file to choose; a `video` does. The button follows the `src`
    field, so it appears exactly when that field exists."""
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
    """On opening, the window came up at whatever size Tkinter gave it: the widget
    list and the properties ate the width and the preview was left a ~60 px strip for
    a 320x1480 panel. The complaint about everything looking like a thumbnail was
    fixed for the MAXIMISED case -- but freshly opened it was still wrong, which is
    100% of the times it is opened."""
    ventana.root.update()
    alto_pantalla = ventana.root.winfo_screenheight()
    pedido = ventana._geometria_inicial(1920, alto_pantalla)
    ancho, alto = (int(v) for v in pedido.split("+")[0].split("x"))
    assert alto >= min(900, int(alto_pantalla * 0.8)), pedido
    assert ancho >= 1000, pedido
    # and no larger than the screen, which would put the footer's buttons out of sight
    assert alto <= alto_pantalla
    assert ancho <= 1920


def test_the_initial_size_fits_a_small_screen(ventana):
    """On a 1366x768 laptop screen it cannot ask for 950 of height: the action bar
    would end up below the edge and there would be no way to save."""
    pedido = ventana._geometria_inicial(1366, 768)
    ancho, alto = (int(v) for v in pedido.split("+")[0].split("x"))
    assert ancho <= 1366 and alto <= 768
