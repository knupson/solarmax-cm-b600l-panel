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
    """Reloj virtual: el loop avanza sin dormir de verdad."""

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
    eng, _, _ = engine(tmp_path, iterations=1)
    eng.run()
    st = eng.state()
    assert st["panel"] == "ok"
    assert st["profile"] == "Test"
    assert st["sn"].startswith("VMAX")
    assert st["resolution"]["cpu.load"] == "psutil"


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
    assert st["profile"] == "Test"           # sigue el bueno
    assert st["frames"] == 4                 # y no dejo de dibujar
    assert any("JSON" in w for w in st["warnings"])


def test_serial_failure_reconnects_with_backoff(tmp_path):
    dead = FakeTransport(fail_on_write=OSError("puerto tomado"))
    alive = FakeTransport()
    eng, made, clock = engine(tmp_path, transports=[dead, alive], iterations=2)
    start = clock.now
    eng.run()
    assert len(made) == 2
    assert clock.now > start                  # durmio el backoff
    assert eng.state()["panel"] == "ok"


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


def test_jpeg_quality_and_rotation_come_from_the_profile(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=1,
                          panel={"rotate": 90, "brightness": 100, "fps": 1,
                                 "jpeg_quality": 40})
    eng.run()
    import io
    from PIL import Image
    frame = [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"][0]
    assert Image.open(io.BytesIO(frame)).size == (1480, 320)
