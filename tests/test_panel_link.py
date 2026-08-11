import pytest

from vmaxpanel.layout.model import Size
from vmaxpanel.transport.panel_link import (HANDSHAKE, FakeTransport, PanelLink,
                                            brightness_cmd, parse_geometry)


def test_parse_geometry_from_the_real_serial_number():
    assert parse_geometry("VMAXA170320*1480S261001155") == Size(320, 1480)


def test_parse_geometry_handles_other_models():
    assert parse_geometry("VMAXB99480*1920S000000001") == Size(480, 1920)


def test_parse_geometry_falls_back_when_unparseable():
    for sn in ("", "basura", "VMAX***S1", None):
        assert parse_geometry(sn) == Size(320, 1480)


def test_brightness_command_frames_the_value():
    assert brightness_cmd(60) == bytes([0xAA, 0xBB, 60, 0xCC, 0xDD])


def test_brightness_command_clamps():
    assert brightness_cmd(-5)[2] == 0
    assert brightness_cmd(500)[2] == 100


def test_open_sends_the_handshake_and_returns_the_serial_number():
    t = FakeTransport()
    link = PanelLink(t)
    sn = link.open()
    assert t.writes[0] == HANDSHAKE
    assert sn == "VMAXA170320*1480S261001155"
    assert link.geometry == Size(320, 1480)


def test_set_brightness_writes_the_command():
    t = FakeTransport()
    link = PanelLink(t)
    link.open()
    link.set_brightness(40)
    assert t.writes[-1] == bytes([0xAA, 0xBB, 40, 0xCC, 0xDD])


def test_send_frame_writes_the_jpeg_verbatim():
    t = FakeTransport()
    link = PanelLink(t)
    link.open()
    jpeg = b"\xff\xd8\xff" + b"\x00" * 40 + b"\xff\xd9"
    link.send_frame(jpeg)
    assert t.writes[-1] == jpeg          # sin header ni framing propio


def test_send_frame_rejects_data_that_is_not_a_jpeg():
    link = PanelLink(FakeTransport())
    link.open()
    with pytest.raises(ValueError, match="JPEG"):
        link.send_frame(b"no soy un jpeg")


def test_send_frame_rejects_short_data_that_only_looks_like_a_jpeg_by_overlap():
    # b"\xff\xd8\xff\xd9" tiene 4 bytes: los primeros 3 matchean el header
    # FFD8FF y los ultimos 2 matchean el footer FFD9, pero comparten el byte
    # del medio -- no hay ni un solo byte de imagen real. Un chequeo que solo
    # mira jpeg[:3] y jpeg[-2:] sin exigir un largo minimo aprueba esto por
    # accidente de superposicion.
    link = PanelLink(FakeTransport())
    link.open()
    with pytest.raises(ValueError, match="JPEG"):
        link.send_frame(b"\xff\xd8\xff\xd9")


def test_send_frame_before_open_raises():
    with pytest.raises(RuntimeError, match="open"):
        PanelLink(FakeTransport()).send_frame(b"\xff\xd8\xff\xff\xd9")


def test_short_serial_number_read_raises():
    link = PanelLink(FakeTransport(sn="corto"))
    with pytest.raises(OSError, match="SN"):
        link.open()


def test_close_closes_the_transport():
    t = FakeTransport()
    PanelLink(t).close()
    assert t.closed


def test_write_failure_propagates_as_oserror():
    t = FakeTransport(fail_on_write=OSError("puerto tomado"))
    with pytest.raises(OSError):
        PanelLink(t).open()
