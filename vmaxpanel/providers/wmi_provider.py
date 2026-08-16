"""Datos de WMI/CIM: espacio por volumen, uptime, procesos.

Deliberately apart from the sidecar. The sidecar exists for what needs
LibreHardwareMonitor or Gigabyte's GSA1 interface; this is plain CIM, queried
without elevation and without third-party DLLs.

**With its own cache, refreshed OFF the render thread.** The engine calls
`registry.read()` from inside `_render_once()`, so anything slow here stops
frames from going out. Measured on the development machine, idle, this query
costs **~550 ms** -- over half a frame's budget at 1 fps -- and its only ceiling
is the 20 s subprocess timeout.

That mattered for real. **The panel resets itself after ~2-3 s without data**
(measured against the hardware: a 2 s gap survives, 5 s and 10 s reset it). While
the machine was extracting a large archive, the two things this query needs --
spawning a process and asking the storage stack about free space -- were exactly
what the saturated disk made wait, so it blew past the watchdog and the panel
restarted, once every TTL, for as long as the load lasted. Nothing showed up
anywhere: the write never failed, so the engine logged no error, and it is the
panel's display controller that resets and not its USB bridge, so Windows never
saw a re-enumeration either.

So the blocking call is gone from the caller's path: `read()` returns whatever
was last fetched and a daemon thread refreshes in the background. The first
fetch is still synchronous, because it happens once at start-up (`probe()`) and
`metrics()` cannot report which volumes exist before it.
"""
import subprocess
import threading
import time

from ..metrics import MetricSpec, spec_for
from .base import Provider

TTL = 30.0

# How stale the cache may get before the data stops being served at all. Serving
# the last good reading through a hiccup is the point of the background refresh;
# serving it forever is how a panel ends up showing a number that stopped being
# true hours ago and nobody notices -- the RAM speed baked into a profile, again.
# Beyond this the metrics go unavailable, which the panel draws as dashes.
MAX_STALE = 10 * TTL

# A single PowerShell for all three queries: starting the process costs more than
# the queries themselves.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
# Volume labels are free text and routinely carry accents. Without this PowerShell
# writes the OEM codepage to the pipe while Python reads the ANSI one, and the
# labels come back as mojibake -- or, on a byte cp1252 leaves undefined, as a
# UnicodeDecodeError that takes every disk metric with it.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$vol = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
  [pscustomobject]@{ letra = $_.DeviceID.TrimEnd(':'); etiqueta = [string]$_.VolumeName
                     libre = [math]::Round($_.FreeSpace / 1GB, 2)
                     total = [math]::Round($_.Size / 1GB, 2) } })
$os = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{
  volumenes = $vol
  uptime    = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalSeconds)
  procesos  = $os.NumberOfProcesses
} | ConvertTo-Json -Compress -Depth 4
"""

MEDIDAS = ("free", "used", "total", "load")


def _consultar_cim() -> dict:
    """Runs the script and returns the parsed dict. Raises on failure."""
    import json
    p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
                       capture_output=True, text=True, timeout=20,
                       # Explicit, and matching the script above. errors="replace"
                       # so one odd byte costs a character rather than every disk
                       # reading on the panel.
                       encoding="utf-8", errors="replace",
                       creationflags=0x08000000)
    if p.returncode != 0:
        raise OSError(f"CIM failed: {(p.stderr or '').strip()[:120]}")
    datos = json.loads(p.stdout)
    # ConvertTo-Json collapses a single-element list into an object.
    vols = datos.get("volumenes") or []
    if isinstance(vols, dict):
        vols = [vols]
    datos["volumenes"] = vols
    return datos


class WmiProvider(Provider):
    id = "wmi"

    def __init__(self, cim=None, ttl=TTL, max_stale=MAX_STALE):
        self._cim = cim or _consultar_cim
        self._ttl = ttl
        self._max_stale = max_stale
        self._datos = None
        self._leido = 0.0
        self._lock = threading.Lock()
        self._refrescando = False
        self.unavailable_reason = None

    # --- cache ---

    def _traer(self):
        """The query itself, plus bookkeeping. Runs on whichever thread calls it."""
        try:
            datos = self._cim()
        except Exception as e:
            # The last good reading is kept: one failed query should not blank
            # every disk on the panel. _actual() is what decides when it has
            # gone too stale to keep showing.
            with self._lock:
                self._refrescando = False
                self.unavailable_reason = f"could not query WMI: {e}"
            raise
        with self._lock:
            self._datos = datos
            self._leido = time.time()
            self._refrescando = False
            self.unavailable_reason = None
        return datos

    def _refrescar_en_fondo(self):
        with self._lock:
            if self._refrescando:
                return              # one in flight is enough
            self._refrescando = True
        # daemon: a refresh in progress must never hold up interpreter shutdown,
        # and the subprocess it is waiting on can take up to its 20 s timeout.
        h = threading.Thread(target=self._intento_de_fondo, daemon=True,
                             name="wmi-refresh")
        h.start()

    def _intento_de_fondo(self):
        try:
            self._traer()
        except Exception:
            pass                    # already recorded in unavailable_reason

    def _actual(self):
        """The cached reading. NEVER blocks once there is one.

        The first call is the exception: it is start-up, and returning None there
        would leave metrics() unable to say which volumes exist -- Registry reads
        that once, in its constructor, so a volume missing from it never appears
        again for the whole run.
        """
        with self._lock:
            datos, leido = self._datos, self._leido
        if datos is None:
            return self._traer()
        edad = time.time() - leido
        if edad >= self._ttl:
            self._refrescar_en_fondo()
        if edad > self._max_stale:
            # Too old to keep passing off as current.
            raise OSError(f"the WMI reading is {edad:.0f} s old and the refresh "
                          f"is not coming back")
        return datos

    def probe(self) -> bool:
        try:
            self._actual()
        except Exception as e:
            self.unavailable_reason = f"could not query WMI: {e}"
            return False
        self.unavailable_reason = None
        return True

    # --- what it serves ---

    def _volumenes(self):
        try:
            return self._actual().get("volumenes") or []
        except Exception:
            return []

    def metrics(self) -> set[str]:
        ids = {"sys.uptime", "sys.procs"}
        for v in self._volumenes():
            for medida in MEDIDAS:
                ids.add(f"vol.{v['letra']}.{medida}")
        return ids

    def read(self):
        try:
            datos = self._actual()
        except Exception as e:
            self.unavailable_reason = f"could not query WMI: {e}"
            return {}
        out = {"sys.uptime": float(datos.get("uptime") or 0),
               "sys.procs": float(datos.get("procesos") or 0)}
        for v in datos.get("volumenes") or []:
            letra, libre, total = v["letra"], float(v["libre"]), float(v["total"])
            out[f"vol.{letra}.free"] = libre
            out[f"vol.{letra}.total"] = total
            out[f"vol.{letra}.used"] = round(total - libre, 2)
            out[f"vol.{letra}.load"] = round(100.0 * (total - libre) / total, 2) \
                if total else None
        return out

    # --- friendly names, for the editor ---

    def _nombre(self, v) -> str:
        """"D: (GAMES)", or "C:" when the volume has no label.

        Without the empty parentheses: a "C: ()" reads as a bug."""
        etiqueta = (v.get("etiqueta") or "").strip()
        return f"{v['letra']}: ({etiqueta})" if etiqueta else f"{v['letra']}:"

    def catalog(self) -> dict:
        """id -> MetricSpec carrying the label the user sees.

        The family in metrics.py already builds "D: — free"; here the name the
        user gave the volume is added, which is the one piece that cannot be
        derived from the id.
        """
        cat = {}
        for mid in ("sys.uptime", "sys.procs"):
            base = spec_for(mid)
            if base is not None:
                cat[mid] = base
        for v in self._volumenes():
            nombre = self._nombre(v)
            for medida in MEDIDAS:
                mid = f"vol.{v['letra']}.{medida}"
                base = spec_for(mid)
                if base is None:
                    continue
                sufijo = base.label.split("—", 1)[-1].strip()
                cat[mid] = MetricSpec(mid, f"{nombre} — {sufijo}", base.unit,
                                      base.kind, base.min, base.max)
        return cat

    def groups(self) -> dict:
        """id -> the device it belongs to, for grouping in the editor."""
        g = {"sys.uptime": "Sistema", "sys.procs": "Sistema"}
        for v in self._volumenes():
            for medida in MEDIDAS:
                g[f"vol.{v['letra']}.{medida}"] = f"Disk {self._nombre(v)}"
        return g
