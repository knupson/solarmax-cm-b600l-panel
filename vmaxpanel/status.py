"""The panel's status, published to a file so it can be read from outside.

**The gap it closes.** The tray has the status in its menu, but from a console --
or from a script, or from another session -- the only observable things were the
log and the process's CPU usage, and going from there to "it is drawing" is a leap
of faith. It went wrong three times in one day: verifying the panel worked by
watching a pythonw process's CPU.

**A file, not a socket or a named pipe.** The reader does not need to talk to the
process, only to know what it sees: a file does that without opening a port,
without odd permissions, without a stuck reader affecting the engine and without
the engine having to serve anyone. The cost is that the reading is a few seconds
old, which is why the age is always reported.

**It is written with an atomic replace** (temp file + os.replace): a reader
arriving mid-write sees the whole old file, never half of the new one.
"""
import json
import os
import time
from pathlib import Path

PERIODO = 5.0            # how often the engine publishes
VIEJO = 30.0             # past this, the reading is called out as stale


class StatusFile:
    """The status file. It never raises: this is diagnostics, not functionality.

    A full disk or a denied permission must not be able to kill the engine thread
    or the tray -- it would be absurd for the mechanism that tells you whether the
    panel works to be the thing that switches it off.
    """

    def __init__(self, path, clock=None):
        self.path = Path(path)
        self._clock = clock or time.time

    def write(self, estado) -> bool:
        datos = dict(estado)
        datos["ts"] = self._clock()
        datos["pid"] = os.getpid()
        tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(datos, ensure_ascii=False, default=str),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def read(self):
        """The last published status, or None if there is none or it is unreadable."""
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def _vivo(pid) -> bool:
    """Whether that pid still exists and looks like a Python process.

    The second part matters: pids get recycled, and a stale status file whose pid
    is now a notepad would report "alive" with another run's data.
    """
    try:
        import psutil
        return "python" in psutil.Process(int(pid)).name().lower()
    except Exception:
        return False


def _fps(v) -> str:
    """30.0 -> "30", 0.5 -> "0.5".

    The model stores fps as a float on purpose: 0.5 is one frame every two seconds
    and is the cheapest cadence there is. But "30.0 fps" on screen is the internal
    type showing through, and that gets fixed at display time -- not by changing the
    contract over something cosmetic, which is exactly what I was about to do.
    """
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "?"
    return f"{v:g}"


def describe(estado, ahora=None, vivo=None) -> str:
    """The status as text, ready to print. It never raises."""
    if not estado:
        return ("the panel is not running (there is no status file). "
                "Start it with the PanelVitals task or with "
                "'python -m vmaxpanel.tray'.")
    ahora = ahora if ahora is not None else time.time()
    vivo = vivo or _vivo
    edad = max(0.0, ahora - float(estado.get("ts") or 0))
    pid = estado.get("pid")

    lineas = []
    if not vivo(pid):
        # First and explicit: with the process gone, everything below is history,
        # and showing "drawing, 12000 frames" without saying so would be a lie.
        lineas.append(f"process {pid}, which wrote this, NO LONGER EXISTS: "
                      f"what follows is the last snapshot before it went away.")
    if estado.get("paused"):
        cabeza = "PAUSED (the port is free)"
    elif estado.get("running"):
        cabeza = "drawing"
    else:
        cabeza = "STOPPED"
    # No em dash and nothing outside ASCII: this prints to the Windows console,
    # which on a Spanish-language system is cp850 and has no U+2014. It came out as
    # a "?" in the one output that exists to diagnose things.
    lineas.append(f"{cabeza} - profile {estado.get('profile') or '?'}, "
                  f"panel {estado.get('panel') or '?'}, "
                  f"{estado.get('frames') or 0} frames, "
                  f"{_fps(estado.get('fps'))} fps")
    # Only when there were any: a "0 reconnections" on every healthy run is noise,
    # and the whole point of this output is that anything printed is worth reading.
    reconexiones = estado.get("reconnects") or 0
    if reconexiones:
        lineas.append(f"{reconexiones} reconnection(s) since it started - the panel "
                      f"re-does the handshake on each one, which looks like a restart")
    lineas.append(f"published {edad:.0f} s ago (pid {pid})")
    if edad > VIEJO and estado.get("running"):
        # A process can be alive and not publishing: an engine wedged in a write to
        # the port still exists. The age is the only signal there is, and saying so
        # is the difference between a stale number and a diagnosis.
        lineas.append(f"CAREFUL: it should publish every {PERIODO:.0f} s. At "
                      f"{edad:.0f} s behind, the engine may be stuck.")
    for p in (estado.get("problems") or []):
        lineas.append(f"  - {p}")
    return "\n".join(lineas)
