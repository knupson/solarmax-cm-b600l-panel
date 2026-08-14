import pytest

from vmaxpanel.layout import model
from vmaxpanel.layout.model import Size
from vmaxpanel.transport.panel_link import (HANDSHAKE, FakeTransport, PanelLink,
                                            SerialTransport, brightness_cmd,
                                            parse_geometry)


def test_parse_geometry_from_the_real_serial_number():
    assert parse_geometry("VMAXA170320*1480S261001155") == Size(320, 1480)


def test_parse_geometry_handles_a_hypothetical_second_model():
    # Este SN es sintetico -- no corresponde a ningun dispositivo real
    # observed, and its 3-digit width is an assumption, not verified data. The only
    # serial confirmed on real hardware is the one in
    # test_parse_geometry_from_the_real_serial_number. Do not "protect" this value
    # as though it were ground truth if the regex ever changes.
    assert parse_geometry("VMAXB99480*1920S000000001") == Size(480, 1920)


def test_parse_geometry_falls_back_when_unparseable():
    for sn in ("", "basura", "VMAX***S1", None):
        assert parse_geometry(sn) == Size(320, 1480)


def test_parse_geometry_falls_back_when_the_parsed_width_is_implausible():
    # "...1024*768..." is a hypothetical 4-digit width: the 3-digit regex
    # digitos fijos (ver comentario junto a _GEOM_RE en panel_link.py) lo
    # truncates to "024" -> 24. 24 is not a plausible width for any known HL-VMAX
    # panel; the plausibility floor has to reject it and fall back to the default
    # instead of propagating the truncated value.
    assert parse_geometry("VMAXQ1024*768S1") == Size(320, 1480)


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
    assert t.writes[-1] == jpeg          # no header and no framing of its own


def test_send_frame_rejects_data_that_is_not_a_jpeg():
    link = PanelLink(FakeTransport())
    link.open()
    with pytest.raises(ValueError, match="JPEG"):
        link.send_frame(b"no soy un jpeg")


def test_send_frame_rejects_short_data_that_only_looks_like_a_jpeg_by_overlap():
    # b"\xff\xd8\xff\xd9" is 4 bytes: the first 3 match the FFD8FF header and the
    # last 2 match the FFD9 footer, but they share the middle byte -- there is not
    # one byte of real image. A check that only looks at jpeg[:3] and jpeg[-2:]
    # without requiring a minimum length approves this by
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
    with pytest.raises(OSError, match="serial"):
        link.open()


def test_close_closes_the_transport():
    t = FakeTransport()
    PanelLink(t).close()
    assert t.closed


def test_write_failure_propagates_as_oserror():
    t = FakeTransport(fail_on_write=OSError("puerto tomado"))
    with pytest.raises(OSError):
        PanelLink(t).open()


# --- lecturas sucias al reconectar ---


def test_a_serial_number_that_is_not_printable_is_rejected():
    """If the process died mid-handshake, old bytes are left in the port's buffer:
    on reconnecting, read(26) returns them at exactly the expected length, so they
    passed as a serial. Result: a garbage serial in the status and the geometry
    falling back to the default by luck. A serial that is not printable ASCII is a
    dirty read, and raising is the right move: the engine reconnects and next time
    the buffer is already clean."""
    basura = PanelLink(FakeTransport(sn=bytes(range(1, 27))))
    with pytest.raises(OSError) as e:
        basura.open()
    assert "serial" in str(e.value).lower()


def test_a_real_serial_number_still_opens():
    link = PanelLink(FakeTransport())
    sn = link.open()
    assert sn.startswith("VMAX")
    assert link.geometry == Size(320, 1480)


def test_the_port_buffers_are_cleared_before_the_handshake():
    """The underlying fix: on opening the port, whatever was left is discarded.
    Without this the first read after a dirty restart can bring back the tail of the
    sesion anterior."""
    hechos = []

    class SerialFalso:
        def __init__(self, *a, **kw):
            self.is_open = True

        def reset_input_buffer(self):
            hechos.append("in")

        def reset_output_buffer(self):
            hechos.append("out")

        def write(self, d):
            pass

        def flush(self):
            pass

        def read(self, n):
            return b""

        def close(self):
            pass

    t = SerialTransport("COM9", abrir=lambda *a, **kw: SerialFalso())
    assert hechos == ["in", "out"], hechos
    t.close()
