"""Providers que leen del mismo SidecarClient, cada uno su namespace."""
from .base import Provider


class _SidecarProvider(Provider):
    namespace = "?"
    served: set[str] = set()
    reason = "el sidecar no reporto esta capacidad"

    def __init__(self, client):
        self._c = client

    def probe(self) -> bool:
        if not self._c.caps().get(self.namespace, False):
            self.unavailable_reason = self.reason
            return False
        self.unavailable_reason = None
        return True

    def metrics(self) -> set[str]:
        return set(self.served)

    def read(self):
        return self._c.namespace(self.namespace)


class Gsa1Provider(_SidecarProvider):
    id = "gsa1"
    namespace = "gsa1"
    served = {"cpu.temp", "cpu.vrm_temp", "cpu.vcore"}
    reason = ("requiere placa Gigabyte con la interfaz ACPI-WMI GSA1 "
              "(clase GSA1_ACPIMethod)")


class PdhProvider(_SidecarProvider):
    id = "pdh"
    namespace = "pdh"
    served = {"cpu.clock", "cpu.name"}
    reason = "no se pudo leer el contador PDH % Processor Performance"


class LhmProvider(_SidecarProvider):
    """GPU y temps de SSD. Los ids de disco se descubren de la muestra."""

    id = "lhm"
    namespace = "lhm"
    _FIXED = {"gpu.name", "gpu.load", "gpu.temp", "gpu.hotspot",
              "gpu.clock", "gpu.power", "gpu.vram", "gpu.fan"}
    reason = ("no se pudo abrir LibreHardwareMonitor "
              "(falta LibreHardwareMonitorLib.dll o HidSharp.dll al lado)")

    @property
    def served(self):
        disks = {k for k in self._c.namespace("lhm") if k.startswith("disk.temp.")}
        return self._FIXED | disks

    def metrics(self) -> set[str]:
        return set(self.served)
