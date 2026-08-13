"""The HL-VMAX panel protocol.

Reverse-engineered by hooking WriteFile inside the LCD Control process:

    open  \\\\.\\COMx              CDC; the baud rate is irrelevant
    TX    F0 A5 5A 0F            handshake
    RX    <serial, 26 ascii bytes>
    TX    AA BB <brightness 0..100> CC DD
    TX    <JPEG>                  one write per frame, no header and no framing

The port is NOT hardcoded: it is autodetected by VID/PID, because on another
machine it is not COM3. The geometry comes out of the serial number itself.
"""
import re
import sys

from ..layout.model import Size

VID, PID = 0x33C3, 0xF101
HANDSHAKE = bytes([0xF0, 0xA5, 0x5A, 0x0F])
SN_LEN = 26

# Documented fallback for a serial that does not match the expected pattern. It
# happens to be the real panel this was written against (CM-B600L, 320x1480), but
# here it is only the default -- nothing else in this module assumes it.
DEFAULT_GEOMETRY = Size(320, 1480)

# A real serial reads "VMAXA170320*1480S261001155": the digits preceding the '*'
# are "170320", not "320" -- the model/revision code ("A17") ends in digits and
# runs straight into the width, which is the last 3 digits touching the '*'. A
# generic \d{2,5} (which takes the longest run of digits that still lets the '*'
# match) eats part of that prefix: on this very serial it returns width=70320
# instead of 320, and on "...B99480*1920..." it returns 99480 instead of 480 --
# it fails its own test cases, this is not a subtle bug.
#
# Pinning the width to exactly 3 digits is what makes the regex work on both known
# serials (320 and 480, both 3 digits) without eating the prefix. It is an
# assumption about the format, not something derived from a specification, and it
# does NOT generalise safely: a future model with a 4-digit width (say 1024) does
# not fall back on its own, it matches anyway and truncates to its last 3 digits
# ("024" -> 24). There is no way to tell "model prefix" from "width" in this
# serial with a regex alone, because there is no separator between them -- they
# are the same run of digits.
#
# The only thing confirmed on a real device is "VMAXA170320*1480S261001155" ->
# 320x1480. It is also ambiguous whether the field is variable-width or 4 digits
# with a leading zero ("A17"+"0320" vs "A170"+"320" are indistinguishable in this
# single sample, because int() drops the leading zero either way); \d{3} is the
# reading that also passes the second serial in the tests (synthetic, not verified
# on hardware), so it is preferred over \d{4} without that making it "the" correct
# reading.
#
# What makes an unknown model safe is not that this regex is demonstrably correct
# -- it is not, see above -- but the plausibility floor (_MIN_PLAUSIBLE_DIM in
# parse_geometry) plus the warning on stderr: a truncated width like 24 is
# discarded and falls back to the default instead of producing a corrupted frame,
# and anybody watching the console finds out it happened. The height side does not
# have the run-on prefix problem (nothing precedes it without a separator in the
# two known serials), so it is left flexible at 2 to 5 digits.
_GEOM_RE = re.compile(r"(\d{3})\s*\*\s*(\d{2,5})")

# Plausibility floor: no known HL-VMAX panel (320x1480, 480x1920) has a side under
# 100 px, and 100 sits comfortably below the smallest confirmed one (320) without
# coming near the clearly truncated values (24, 80) that appear when \d{3} eats a
# digit from a real 4-digit width. It does not claim to be an exact physical limit,
# only a cheap cut that catches the truncation documented above.
_MIN_PLAUSIBLE_DIM = 100


class PanelNotFound(Exception):
    pass


def _sn_plausible(sn) -> bool:
    """The serial has to be printable text. Neither the "VMAX" prefix nor the
    geometry format is required: parse_geometry() takes care of that, and it
    already warns and falls back to the default. Here only what cannot be a serial
    number of any brand is discarded -- control bytes, binary garbage -- which is
    the shape a dirty read from the port has."""
    return bool(sn) and all(32 <= ord(c) < 127 for c in sn)


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
        # sn was not empty: somebody connected something that gave a real serial,
        # but it did not match the pattern or gave an implausible geometry (see the
        # truncation documented above _GEOM_RE). None/"" do not reach here -- that
        # is the ordinary "there is nothing to parse" case and does not warrant a
        # warning.
        print(f'warning: could not parse the panel geometry out of the serial '
              f'"{sn}"; falling back to {DEFAULT_GEOMETRY.width}x'
              f'{DEFAULT_GEOMETRY.height}', file=sys.stderr)
    return DEFAULT_GEOMETRY


def brightness_cmd(v: int) -> bytes:
    return bytes([0xAA, 0xBB, max(0, min(100, int(v))), 0xCC, 0xDD])


class SerialTransport:
    """pyserial behind the minimal interface PanelLink needs."""

    def __init__(self, port, timeout=1.5, write_timeout=8, abrir=None):
        if abrir is None:
            import serial
            abrir = serial.Serial
        self._ser = abrir(port, 9600, timeout=timeout,
                          write_timeout=write_timeout)
        self.port = port
        # Whatever was left in the port's buffers is discarded. Opening a COM port
        # does NOT clear them: if the previous process died mid-handshake -- a
        # hang, a --stop at just the wrong moment -- that session's queue is still
        # there, and open()'s read(26) returns it at exactly the expected length,
        # so it passes as a serial number. Silently: garbage serial in the status,
        # and the geometry falling back to the default by luck.
        for limpiar in ("reset_input_buffer", "reset_output_buffer"):
            try:
                getattr(self._ser, limpiar)()
            except Exception:
                pass                # a transport without them is not a problem

    def write(self, data):
        self._ser.write(data)
        self._ser.flush()

    def read(self, n):
        return self._ser.read(n)

    def close(self):
        # Best effort: close() is called from cleanup (finally, __exit__) and must
        # not mask the original exception that probably got us here.
        # KeyboardInterrupt/SystemExit are not Exception and keep propagating.
        try:
            self._ser.close()
        except Exception:
            pass


class FakeTransport:
    """Test transport: it captures writes and returns a fixed serial.

    It simulates a device answering in a single block; it does not reproduce reads
    fragmented across several small read() calls. That is pyserial's job (its
    read(n) already polls internally until it has n bytes or the timeout expires),
    not something PanelLink has to reimplement.
    """

    def __init__(self, sn="VMAXA170320*1480S261001155", fail_on_write=None):
        self.writes = []
        self.closed = False
        # bytes or str: the tests need to be able to inject a response that is NOT
        # valid text, which is exactly the shape of a dirty read from the port.
        self._sn = sn if isinstance(sn, (bytes, bytearray)) else sn.encode("ascii", "replace")
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
        # If more than one panel is connected, the first is taken silently. With a
        # single physical panel per case this does not matter in practice; if
        # disambiguating is ever needed, find_panel_ports() already returns the
        # whole list for the caller to decide.
        ports = [port] if port else find_panel_ports()
        if not ports:
            raise PanelNotFound(
                f"no HL-VMAX panel found (VID_{VID:04X}/PID_{PID:04X}). Check that it "
                f"is plugged in and that LCD Control has not taken it.")
        return cls(SerialTransport(ports[0]))

    def open(self) -> str:
        self._t.write(HANDSHAKE)
        raw = self._t.read(SN_LEN)
        # != and not just < : a transport returning too much (it should not,
        # read(n) is "at most n bytes", and FakeTransport and pyserial honour that
        # contract by construction, so this branch cannot be exercised with what
        # this module has) is not a valid serial either.
        if len(raw) != SN_LEN:
            raise OSError(f"the panel returned a serial of unexpected length "
                          f"({len(raw)} of {SN_LEN} bytes); it may be held by "
                          f"another process")
        sn = raw.decode("ascii", "replace")
        if not _sn_plausible(sn):
            # A dirty read (see the buffer reset in SerialTransport) or a device
            # that is not the panel. Raising is the right move: the engine
            # reconnects, and with clean buffers the next read is the good one.
            # Accepting it would leave a garbage serial in the status and a
            # geometry chosen at random, which is worse than one retry.
            raise OSError(f"the panel returned something that is not a serial number "
                          f"({sn!r}): it may be a dirty read from the port, or "
                          f"another device")
        self.serial_number = sn
        self.geometry = parse_geometry(self.serial_number)
        return self.serial_number

    def set_brightness(self, v: int):
        self._t.write(brightness_cmd(v))

    def send_frame(self, jpeg: bytes):
        if self.serial_number is None:
            raise RuntimeError("open() has to be called before sending frames")
        # len(jpeg) >= 5 stops a 4-byte input from passing the check by overlap:
        # jpeg[:3] and jpeg[-2:] share a byte when the total is shorter than
        # header+footer (3+2), so without the minimum length b"\xff\xd8\xff\xd9"
        # (4 bytes, not one byte of real image) would be approved as a complete
        # jpeg.
        if len(jpeg) < 5 or jpeg[:3] != b"\xff\xd8\xff" or jpeg[-2:] != b"\xff\xd9":
            raise ValueError("the frame is not a complete JPEG "
                             "(it has to start with FFD8FF and end with FFD9)")
        self._t.write(jpeg)

    def close(self):
        self._t.close()
