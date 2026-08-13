"""Datos de WMI/CIM: espacio por volumen, uptime, procesos.

Deliberately apart from the sidecar. The sidecar exists for what needs
LibreHardwareMonitor or Gigabyte's GSA1 interface; this is plain CIM, queried
without elevation and without third-party DLLs.

**With its own cache.** Querying the volumes costs ~300 ms measured: at 1 fps
that is a third of a frame's budget, and a disk's free space does not change
between frames. The engine has a single sampling cadence for every provider, so
the one that knows what its own reading costs is the provider.
"""
import subprocess
import time

from ..metrics import MetricSpec, spec_for
from .base import Provider

TTL = 30.0

# A single PowerShell for all three queries: starting the process costs more than
# the queries themselves.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
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

    def __init__(self, cim=None, ttl=TTL):
        self._cim = cim or _consultar_cim
        self._ttl = ttl
        self._datos = None
        self._leido = 0.0
        self.unavailable_reason = None

    # --- cache ---

    def _actual(self):
        ahora = time.time()
        if self._datos is not None and ahora - self._leido < self._ttl:
            return self._datos
        self._datos = self._cim()
        self._leido = ahora
        return self._datos

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
