"""Protocolo del panel HL-VMAX.

Reverseado hookeando WriteFile en el proceso de LCD Control:

    open  \\\\.\\COMx              CDC; el baud es irrelevante
    TX    F0 A5 5A 0F            handshake
    RX    <SN ascii, 26 bytes>    "VMAXA170320*1480S261001155"
    TX    AA BB <brillo 0..100> CC DD
    TX    <JPEG>                  un write por frame, sin header ni framing

El puerto NO se hardcodea: se autodetecta por VID/PID, porque en otra maquina no
es COM3. La geometria sale del propio SN.
"""
import re
import sys

from ..layout.model import Size

VID, PID = 0x33C3, 0xF101
HANDSHAKE = bytes([0xF0, 0xA5, 0x5A, 0x0F])
SN_LEN = 26

# Fallback documentado para un SN que no matchea el patron esperado. Es el
# panel real de esta maquina (CM-B600L, 320x1480), pero aca es solo el valor
# de default -- no se asume en ningun otro lado del modulo.
DEFAULT_GEOMETRY = Size(320, 1480)

# El SN real es "VMAXA170320*1480S261001155": los digitos que preceden al '*'
# son "170320", no "320" -- el codigo de modelo/revision ("A17") termina en
# digitos y se pega sin separador al ancho, que son los ultimos 3 digitos que
# tocan el '*'. Un \d{2,5} generico (busca la corrida de digitos mas larga
# que todavia deje matchear el '*') se come parte de ese prefijo: sobre este
# mismo SN devuelve ancho=70320 en vez de 320, y sobre "...B99480*1920..."
# devuelve 99480 en vez de 480 -- falla sus propios casos de prueba, no es
# un bug sutil.
#
# Fijar el ancho en exactamente 3 digitos es lo que hace que el regex ande
# con los dos SN conocidos (320 y 480, ambos de 3 digitos) sin comerse el
# prefijo. Es una asuncion sobre el formato, no algo derivado de una
# especificacion, y NO generaliza de forma segura: un modelo futuro con un
# ancho de 4 digitos (ej. 1024) no cae al default por si solo, matchea igual
# y trunca a sus ultimos 3 digitos ("024" -> 24). No hay forma de distinguir
# "prefijo de modelo" de "ancho" en este SN con un regex solo, porque no hay
# separador entre ambos -- son la misma corrida de digitos.
#
# Lo unico confirmado en un dispositivo real es "VMAXA170320*1480S261001155"
# -> 320x1480. Tambien es ambiguo si el campo es de ancho variable o de 4
# digitos con cero a la izquierda ("A17"+"0320" vs "A170"+"320" son
# indistinguibles en esta unica muestra, porque int() descarta el cero
# inicial en cualquiera de las dos lecturas); \d{3} es la lectura que hace
# pasar tambien el segundo SN de los tests (sintetico, no verificado en
# hardware), asi que se prefiere sobre \d{4} sin que eso la vuelva "la"
# lectura correcta.
#
# Lo que hace que un modelo desconocido sea seguro no es que este regex sea
# demostrablemente correcto -- no lo es, ver arriba -- sino el piso de
# plausibilidad (_MIN_PLAUSIBLE_DIM en parse_geometry) mas el aviso por
# stderr: un ancho truncado como 24 se descarta y cae al default en vez de
# producir un frame corrompido, y alguien mirando la consola se entera de
# que paso. El lado del alto no tiene el problema del prefijo pegado (nada
# lo precede sin separador en los dos SN conocidos), asi que se deja
# flexible en 2 a 5 digitos.
_GEOM_RE = re.compile(r"(\d{3})\s*\*\s*(\d{2,5})")

# Piso de plausibilidad: ningun panel HL-VMAX conocido (320x1480, 480x1920)
# tiene un lado menor a 100px, y 100 esta comodo por debajo del mas chico
# confirmado (320) sin acercarse a valores claramente truncados (24, 80) que
# aparecen cuando \d{3} le come 1 digito a un ancho real de 4. No pretende
# ser un limite fisico exacto, solo un corte barato que atrapa el caso de
# truncamiento documentado arriba.
_MIN_PLAUSIBLE_DIM = 100


class PanelNotFound(Exception):
    pass


def find_panel_ports() -> list[str]:
    from serial.tools import list_ports
    return [p.device for p in list_ports.comports()
            if p.vid == VID and p.pid == PID]


def parse_geometry(sn) -> Size:
    if isinstance(sn, str) and sn:
        m = _GEOM_RE.search(sn)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if w >= _MIN_PLAUSIBLE_DIM and h >= _MIN_PLAUSIBLE_DIM:
                return Size(w, h)
        # sn no vino vacio: alguien conecto algo que dio un SN real, pero no
        # matcheo el patron o dio una geometria implausible (ver el
        # truncamiento documentado arriba de _GEOM_RE). None/"" no entran
        # aca -- ese es el caso ordinario de "no hay nada que parsear" y no
        # amerita aviso.
        print(f'aviso: no se pudo parsear la geometria del panel desde el SN '
              f'"{sn}"; usando default {DEFAULT_GEOMETRY.width}x'
              f'{DEFAULT_GEOMETRY.height}', file=sys.stderr)
    return DEFAULT_GEOMETRY


def brightness_cmd(v: int) -> bytes:
    return bytes([0xAA, 0xBB, max(0, min(100, int(v))), 0xCC, 0xDD])


class SerialTransport:
    """pyserial detras de la interfaz minima que PanelLink necesita."""

    def __init__(self, port, timeout=1.5, write_timeout=8):
        import serial
        self._ser = serial.Serial(port, 9600, timeout=timeout,
                                  write_timeout=write_timeout)
        self.port = port

    def write(self, data):
        self._ser.write(data)
        self._ser.flush()

    def read(self, n):
        return self._ser.read(n)

    def close(self):
        # Best-effort: close() se llama desde limpieza (finally, __exit__) y
        # no tiene que tapar la excepcion original que probablemente hizo
        # que se llegue hasta aca. KeyboardInterrupt/SystemExit no son
        # Exception y siguen propagandose.
        try:
            self._ser.close()
        except Exception:
            pass


class FakeTransport:
    """Transporte de prueba: captura los writes y devuelve un SN fijo.

    Simula un dispositivo que responde en un solo bloque; no reproduce
    lecturas fragmentadas en varios read() de a poco. Eso es responsabilidad
    de pyserial (su read(n) ya hace polling interno hasta juntar n bytes o
    vencer el timeout), no algo que PanelLink tenga que reimplementar.
    """

    def __init__(self, sn="VMAXA170320*1480S261001155", fail_on_write=None):
        self.writes = []
        self.closed = False
        self._sn = sn.encode("ascii", "replace")
        self._fail = fail_on_write

    def write(self, data):
        if self._fail is not None:
            raise self._fail
        self.writes.append(bytes(data))

    def read(self, n):
        out, self._sn = self._sn[:n], self._sn[n:]
        return out

    def close(self):
        self.closed = True


class PanelLink:
    def __init__(self, transport):
        self._t = transport
        self.serial_number = None
        self.geometry = DEFAULT_GEOMETRY

    @classmethod
    def autodetect(cls, port=None):
        # Si hay mas de un panel conectado, se toma el primero en silencio.
        # Con un solo panel fisico por gabinete esto no importa en la
        # practica; si algun dia hace falta desambiguar, find_panel_ports()
        # ya devuelve la lista completa para que el caller decida.
        ports = [port] if port else find_panel_ports()
        if not ports:
            raise PanelNotFound(
                f"no se encontro un panel HL-VMAX (VID_{VID:04X}/PID_{PID:04X}). "
                f"Revisa que este conectado y que no lo tenga tomado LCD Control.")
        return cls(SerialTransport(ports[0]))

    def open(self) -> str:
        self._t.write(HANDSHAKE)
        raw = self._t.read(SN_LEN)
        # != y no solo < : un transporte que devuelva de mas (no deberia,
        # read(n) es "como maximo n bytes", pero FakeTransport y pyserial
        # respetan ese contrato por construccion, asi que esta rama no tiene
        # forma de probarse con lo que hay en este modulo) tampoco es un SN
        # valido.
        if len(raw) != SN_LEN:
            raise OSError(f"el panel devolvio un SN de tamano inesperado "
                          f"({len(raw)} de {SN_LEN} bytes); puede estar "
                          f"tomado por otro proceso")
        self.serial_number = raw.decode("ascii", "replace")
        self.geometry = parse_geometry(self.serial_number)
        return self.serial_number

    def set_brightness(self, v: int):
        self._t.write(brightness_cmd(v))

    def send_frame(self, jpeg: bytes):
        if self.serial_number is None:
            raise RuntimeError("hay que llamar a open() antes de mandar frames")
        # len(jpeg) >= 5 evita que un input de 4 bytes pase el chequeo por
        # superposicion: jpeg[:3] y jpeg[-2:] comparten byte cuando el total
        # es mas corto que header+footer (3+2), asi que sin el minimo de
        # largo b"\xff\xd8\xff\xd9" (4 bytes, ningun byte de imagen real)
        # se aprobaria como si fuera un jpeg completo.
        if len(jpeg) < 5 or jpeg[:3] != b"\xff\xd8\xff" or jpeg[-2:] != b"\xff\xd9":
            raise ValueError("el frame no es un JPEG completo "
                             "(tiene que abrir en FFD8FF y cerrar en FFD9)")
        self._t.write(jpeg)

    def close(self):
        self._t.close()
