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
    # observado, y su ancho de 3 digitos es una suposicion, no un dato
    # verificado. El unico SN confirmado en hardware real es el de
    # test_parse_geometry_from_the_real_serial_number. No "proteger" este
    # valor como si fuera ground truth si algun dia cambia el regex.
    assert parse_geometry("VMAXB99480*1920S000000001") == Size(480, 1920)


def test_parse_geometry_falls_back_when_unparseable():
    for sn in ("", "basura", "VMAX***S1", None):
        assert parse_geometry(sn) == Size(320, 1480)


def test_parse_geometry_falls_back_when_the_parsed_width_is_implausible():
    # "...1024*768..." es un ancho hipotetico de 4 digitos: el regex de 3
    # digitos fijos (ver comentario junto a _GEOM_RE en panel_link.py) lo
    # trunca a "024" -> 24. 24 no es un ancho plausible para ningun panel
    # HL-VMAX conocido; el piso de plausibilidad tiene que rechazarlo y caer
    # al default en vez de propagar el valor truncado.
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


# --- lecturas sucias al reconectar ---


def test_a_serial_number_that_is_not_printable_is_rejected():
    """Si el proceso murio con un handshake a medias, en el buffer del puerto quedan
    bytes viejos: al reconectar, read(26) los devuelve y tenian el largo justo, asi que
    pasaban como SN. Resultado: numero de serie basura en el estado y la geometria
    cayendo al default por casualidad. Un SN que no es ASCII imprimible es una lectura
    sucia, y levantar es lo correcto: el engine reconecta y la proxima vez el buffer ya
    esta limpio."""
    basura = PanelLink(FakeTransport(sn=bytes(range(1, 27))))
    with pytest.raises(OSError) as e:
        basura.open()
    assert "sn" in str(e.value).lower() or "serie" in str(e.value).lower()


def test_a_real_serial_number_still_opens():
    link = PanelLink(FakeTransport())
    sn = link.open()
    assert sn.startswith("VMAX")
    assert link.geometry == Size(320, 1480)


def test_the_port_buffers_are_cleared_before_the_handshake():
    """El arreglo de fondo: al abrir el puerto se descarta lo que haya quedado. Sin
    esto la primera lectura despues de un reinicio sucio puede traer la cola de la
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
