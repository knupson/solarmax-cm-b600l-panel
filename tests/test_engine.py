import json

import pytest

from vmaxpanel.engine import Engine, EngineConfig
from vmaxpanel.layout import loader
from vmaxpanel.providers.base import Provider
from vmaxpanel.providers.registry import Registry
from vmaxpanel.transport.panel_link import FakeTransport, PanelLink
from tests.test_schema import MINIMAL


class FakeCpu(Provider):
    id = "psutil"

    def __init__(self, value=42.0):
        self.value = value
        self.reads = 0

    def probe(self):
        return True

    def metrics(self):
        return {"cpu.load"}

    def read(self):
        self.reads += 1
        return {"cpu.load": self.value}


class FakeClock:
    """A virtual clock: the loop advances without really sleeping."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += max(0.0, s)


def profile(tmp_path, **over):
    raw = dict(MINIMAL)
    raw.update(over)
    path = tmp_path / "vitals.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def engine(tmp_path, transports=None, iterations=3, **over):
    path = profile(tmp_path, **over)
    store = loader.ProfileStore(path)
    store.load_now()
    made = []

    def factory():
        t = (transports or [FakeTransport()]).pop(0) if transports else FakeTransport()
        made.append(t)
        return PanelLink(t)

    clock = FakeClock()
    cfg = EngineConfig(profile_path=path, max_iterations=iterations)
    eng = Engine(store, Registry([FakeCpu()]), cfg, link_factory=factory, clock=clock)
    return eng, made, clock


def test_run_sends_one_frame_per_iteration(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=3)
    eng.run()
    frames = [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"]
    assert len(frames) == 3
    assert eng.state()["frames"] == 3


def test_run_handshakes_and_sets_brightness_once(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=3)
    eng.run()
    writes = made[0].writes
    assert writes[0] == b"\xf0\xa5\x5a\x0f"
    assert sum(1 for w in writes if w[:2] == b"\xaa\xbb") == 1


def test_state_reports_the_panel_and_the_profile(tmp_path):
    # The "ok" state only makes sense WHILE the link is open: run() closes and
    # discards the link on returning (for any reason, see
    # test_clean_exit_closes_the_link), so state() asked after run() always gives
    # "disconnected". To really test the "connected" state it has to be observed
    # from inside the loop, not afterwards.
    eng, _, _ = engine(tmp_path, iterations=1)
    captured = {}
    original = eng._render_once

    def patched():
        original()
        captured.update(eng.state())

    eng._render_once = patched
    eng.run()
    assert captured["panel"] == "ok"
    assert captured["profile"] == "Test"
    assert captured["sn"].startswith("VMAX")
    assert captured["resolution"]["cpu.load"] == "psutil"


def test_state_lists_unavailable_metrics_with_reasons(tmp_path):
    from vmaxpanel.providers.msr import MsrProvider
    path = profile(tmp_path)
    store = loader.ProfileStore(path)
    store.load_now()
    eng = Engine(store, Registry([FakeCpu(), MsrProvider()]),
                 EngineConfig(profile_path=path, max_iterations=1),
                 link_factory=lambda: PanelLink(FakeTransport()), clock=FakeClock())
    eng.run()
    assert "WinRing0" in eng.state()["unavailable"]["cpu.power"]


def test_frame_rate_respects_the_layout_fps(tmp_path):
    eng, _, clock = engine(tmp_path, iterations=4,
                           panel={"rotate": 0, "brightness": 100, "fps": 2,
                                  "jpeg_quality": 82})
    start = clock.now
    eng.run()
    assert 1.4 <= clock.now - start <= 1.6      # 3 esperas de 0.5 s


def test_sensors_are_sampled_once_per_period_not_once_per_frame(tmp_path):
    path = profile(tmp_path, panel={"rotate": 0, "brightness": 100, "fps": 4,
                                    "jpeg_quality": 82})
    store = loader.ProfileStore(path)
    store.load_now()
    cpu = FakeCpu()
    eng = Engine(store, Registry([cpu]),
                 EngineConfig(profile_path=path, sample_period=1.0, max_iterations=8),
                 link_factory=lambda: PanelLink(FakeTransport()), clock=FakeClock())
    eng.run()
    assert cpu.reads <= 4          # 8 frames a 4 fps = 2 s => 2-3 muestras, no 8


def test_layout_change_is_picked_up_without_restarting(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=4)
    path = tmp_path / "vitals.json"

    original_run = eng._render_once

    def patched():
        if eng.stats["frames"] == 1:
            path.write_text(json.dumps(dict(MINIMAL, name="Recargado")),
                            encoding="utf-8")
        return original_run()

    eng._render_once = patched
    eng.run()
    assert eng.state()["profile"] == "Recargado"


def test_broken_layout_on_reload_keeps_rendering_the_previous_one(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=4)
    path = tmp_path / "vitals.json"

    original_run = eng._render_once

    def patched():
        if eng.stats["frames"] == 1:
            path.write_text("{roto", encoding="utf-8")
        return original_run()

    eng._render_once = patched
    eng.run()
    st = eng.state()
    assert st["profile"] == "Test"           # the good one is still there
    assert st["frames"] == 4                 # y no dejo de dibujar
    assert any("JSON" in w for w in st["warnings"])


def test_serial_failure_reconnects_with_backoff(tmp_path):
    dead = FakeTransport(fail_on_write=OSError("puerto tomado"))
    alive = FakeTransport()
    eng, made, clock = engine(tmp_path, transports=[dead, alive], iterations=2)
    start = clock.now
    eng.run()
    assert len(made) == 2
    assert clock.now > start                  # it slept the backoff
    # The transport whose handshake failed has to be left closed: if nothing closes
    # it, the freshly opened handle leaks on every reconnection attempt instead of
    # being released before the next one.
    assert dead.closed is True
    # "disconnected", no "ok": run() ya termino (se agoto max_iterations
    # AFTER a successful reconnection), so nothing is writing to the panel at this
    # instant. It is the contract, not an oversight -- the field is
    # binario ("ok" | "disconnected") y no existe un tercer estado de
    # "was connected but is not any more". Do not revert this to "ok" believing it
    # means "it connected fine" when it actually describes the CURRENT state.
    assert eng.state()["panel"] == "disconnected"


def test_clean_exit_closes_the_link(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=2)
    eng.run()
    # run() finished without an exception (max_iterations ran out): the transport
    # left open has to be closed anyway, not only when there is an
    # reconexion de por medio.
    assert made[0].closed is True
    # "disconnected", not "ok": the same binary contract the test above documents. A
    # closed link reporting "ok" would be the same class of lying status this whole
    # project exists to avoid (LCD Control showing a CPU load that was not the real
    # one) -- here applied to the connection field instead of to a metric. Do not
    # revert this to "ok" thinking "the last attempt went fine": state() describes
    # the present, not the history.
    assert eng.state()["panel"] == "disconnected"


def test_stop_ends_the_loop(tmp_path):
    eng, _, _ = engine(tmp_path, iterations=None)
    original = eng._render_once

    def patched():
        original()
        if eng.stats["frames"] >= 2:
            eng.stop()

    eng._render_once = patched
    eng.run()
    assert eng.stats["frames"] == 2


def _one_frame(tmp_path, **panel):
    eng, made, _ = engine(tmp_path, iterations=1,
                          panel={"brightness": 100, "fps": 1, **panel})
    eng.run()
    return [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"][0]


def test_jpeg_quality_comes_from_the_profile(tmp_path):
    peor = _one_frame(tmp_path, rotate=0, jpeg_quality=40)
    mejor = _one_frame(tmp_path, rotate=0, jpeg_quality=95)
    assert len(peor) < len(mejor)


def test_rotation_comes_from_the_profile(tmp_path):
    """This used to be tested with rotate 90, asserting the frame came out
    1480x320. That is exactly the misshapen frame a 320x1480 panel cannot show and
    that the final review flagged as a defect: the engine now
    niega a mandarlo (ver test_a_rotation_that_does_not_fit_the_panel...).
    The original intent -- that the rotate comes from the profile and not from a
    constant -- is tested just as well with 180, the real rotation of this case, by
    comparing CONTENT rather than size: 0 and 180 both give 320x1480, so the size
    distinguished nothing anyway.
    """
    import io
    from PIL import Image, ImageChops

    def frame_at(rotate):
        data = _one_frame(tmp_path, rotate=rotate, jpeg_quality=95)
        return Image.open(io.BytesIO(data)).convert("RGB")

    derecho, cabeza = frame_at(0), frame_at(180)
    assert derecho.size == cabeza.size == (320, 1480)
    assert ImageChops.difference(derecho, cabeza).getbbox() is not None

    # With tolerance, not exact: JPEG is lossy, so rotating after decoding does not
    # reproduce byte for byte what came out of encoding the already-rotated image.
    # Same rule as the golden test.
    girado = cabeza.transpose(Image.Transpose.ROTATE_180)
    diff = ImageChops.difference(derecho, girado)
    peor = max(max(band.getextrema()) for band in diff.split())
    assert peor <= 40, f"180 is not the same image turned around (delta {peor})"


def test_an_invalid_profile_at_startup_is_picked_up_once_the_user_fixes_it(tmp_path):
    """_connect() raises OSError when there is no layout, and reload_if_changed()
    was only called from _serve(), which is to say after connecting: an engine
    started with a broken profile spun in the backoff forever and never picked up
    the corrected file. The tray starts before the profile is guaranteed, so that is
    the normal case, not the rare one.

    The sleep counter bounds the run: without the fix this is an infinite loop on a
    virtual clock, and a test that hangs reports nothing.
    """
    path = tmp_path / "vitals.json"
    path.write_text("{roto", encoding="utf-8")
    store = loader.ProfileStore(path)
    assert store.load_now()                      # it starts with no valid layout
    assert store.current is None

    made = []

    def factory():
        t = FakeTransport()
        made.append(t)
        return PanelLink(t)

    clock = FakeClock()
    eng = Engine(store, Registry([FakeCpu()]),
                 EngineConfig(profile_path=path, max_iterations=1),
                 link_factory=factory, clock=clock)

    sleeps = []
    real_sleep = clock.sleep

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 1:
            # The user fixes the file while the engine waits.
            path.write_text(json.dumps(MINIMAL), encoding="utf-8")
        if len(sleeps) > 5:
            eng.stop()                           # cortamos: no se recupero
        real_sleep(s)

    clock.sleep = sleep
    eng.run()

    assert eng.stats["frames"] == 1, f"it never re-read the profile ({len(sleeps)} waits)"
    assert store.current is not None
    assert [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"]


def test_a_rotation_that_does_not_fit_the_panel_is_refused_instead_of_sent(tmp_path):
    """rotate 90 on a 320x1480 panel produces a 1480x320 frame that the panel writes
    without complaint: garbage on screen and no errors anywhere. The layout validator
    cannot catch it -- it does not know the panel geometry, and a layout designed
    1480x320 with rotate 90 IS valid for this panel -- so it is checked here, where
    both
    dos cosas.
    """
    path = profile(tmp_path, panel={"rotate": 90, "brightness": 100, "fps": 1,
                                    "jpeg_quality": 82})
    store = loader.ProfileStore(path)
    assert store.load_now() == []            # the layout is valid; the rotation does not fit

    made = []

    def factory():
        t = FakeTransport()
        made.append(t)
        return PanelLink(t)

    clock = FakeClock()
    eng = Engine(store, Registry([FakeCpu()]),
                 EngineConfig(profile_path=path, max_iterations=1),
                 link_factory=factory, clock=clock)

    sleeps = []
    real_sleep = clock.sleep

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) > 3:
            eng.stop()
        real_sleep(s)

    clock.sleep = sleep
    eng.run()

    assert [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"] == []
    assert eng.stats["frames"] == 0
    assert "rotate" in (eng.state()["last_error"] or "")


def test_a_rejected_hot_reload_is_reported_instead_of_silent(tmp_path, capsys):
    """The invariant "a broken JSON does not blank the panel" made a rejected
    profile COMPLETELY silent: the engine kept drawing the old layout and nothing
    warned. It happened twice with the user watching the panel and asking why nothing
    changed, and both times the cause was the same -- a new metric the live process
    does not know about.
    """
    eng, made, _ = engine(tmp_path, iterations=4)
    path = tmp_path / "vitals.json"

    original = eng._render_once

    def patched():
        if eng.stats["frames"] == 1:
            path.write_text(json.dumps(dict(MINIMAL, widgets=[
                {"id": "x", "type": "text", "metric": "no.existe", "x": 1, "y": 1,
                 "font": "mono-14", "color": "#FFFFFF", "format": "{}"}])),
                encoding="utf-8")
        return original()

    eng._render_once = patched
    eng.run()

    # A single call: readouterr() DRAINS the buffer, so a second one returns empty
    # and concatenating the two loses precisely the stream that matters.
    capturado = capsys.readouterr()
    salida = capturado.out + capturado.err
    assert "rejected" in salida.lower(), salida
    assert "no.existe" in salida
    assert eng.state()["profile"] == "Test"      # still on the good one
    assert eng.stats["frames"] == 4              # and without stopping drawing


def test_the_rejection_is_not_logged_once_per_frame(tmp_path, capsys):
    """At 30 fps, one warning per frame is 1800 lines a minute in the log."""
    eng, made, _ = engine(tmp_path, iterations=6)
    path = tmp_path / "vitals.json"
    path.write_text("{roto", encoding="utf-8")
    eng.store.reload_if_changed()
    eng.run()
    capturado = capsys.readouterr()
    salida = capturado.out + capturado.err
    assert salida.lower().count("rejected") <= 1, salida


def test_dropping_the_link_closes_the_renderer(tmp_path):
    """The renderer owns the background, and a video background has an ffmpeg behind
    it. The engine discards the renderer every time the link drops (and when run()
    ends), so if it does not close it, each reconnection leaves an orphan decoder --
    the same pattern this project already had with the sidecar."""
    eng, made, _ = engine(tmp_path, iterations=1)
    eng.run()
    assert made[0].closed
    cerrados = []
    eng._renderer = type("R", (), {"close": lambda self: cerrados.append(True)})()
    eng._drop_link()
    assert cerrados == [True]
    assert eng._renderer is None


def test_state_reports_a_metric_the_layout_uses_and_nobody_serves(tmp_path):
    """The case that stayed silent: a FAMILY metric (fan.9.rpm, core.12.temp,
    vol.Z.free) that the profile uses and no provider serves. Families cannot be
    enumerated, so the Registry cannot list them on its own -- the only thing that
    knows which are really in use is the engine, with the layout in front of it.
    Without this the panel draws dashes and the status says "no data: {}"."""
    raw = dict(MINIMAL)
    raw["widgets"] = MINIMAL["widgets"] + [
        {"id": "fantasma", "type": "text", "metric": "fan.9.rpm", "x": 10, "y": 10,
         "font": "mono-14", "color": "#FFFFFF", "format": "{:.0f}"}]
    eng, _, _ = engine(tmp_path, iterations=2, widgets=raw["widgets"])
    eng.run()
    faltan = eng.state()["unavailable"]
    assert "fan.9.rpm" in faltan, faltan
    assert faltan["fan.9.rpm"].strip()


def test_a_metric_that_is_served_is_not_reported_as_missing(tmp_path):
    eng, _, _ = engine(tmp_path, iterations=2)
    eng.run()
    assert "cpu.load" not in eng.state()["unavailable"]
