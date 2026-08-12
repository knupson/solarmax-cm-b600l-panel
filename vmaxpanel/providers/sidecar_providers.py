"""Providers que leen del mismo SidecarClient, cada uno su namespace."""
from .base import Provider
from .sidecar import STALE_AFTER


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
        """Levanta en vez de devolver datos viejos.

        Registry.read() convierte la excepcion en metrica degradada, con el
        motivo visible en state()["unavailable"], y la vuelve a servir sola
        cuando el read siguiente funciona. Devolver el ultimo namespace
        recibido seria peor que no mostrar nada: un sensors.ps1 colgado --
        bloqueado en un Get-Counter o en una llamada CIM -- deja el panel
        pintando un cpu.temp de hace horas como si fuera de ahora, y nada en
        el estado avisa que esta pasando.

        Las dos condiciones se chequean en cada vuelta a proposito. probe()
        corre una sola vez, en Registry.__init__, asi que era el unico lugar
        que miraba caps: una fuente que se caia en la mitad de la corrida
        seguia contando como disponible, en contra de lo que documenta
        sensors.ps1.
        """
        if not self._c.fresh:
            raise RuntimeError(f"el sidecar no entrega datos desde hace mas "
                               f"de {STALE_AFTER:.0f} s")
        if not self._c.caps().get(self.namespace, False):
            raise RuntimeError(self.reason)
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
    """GPU y temps de SSD. Los ids de disco se descubren de la muestra.

    Peligro de orden: `served` lee la primera muestra que haya llegado del
    sidecar. `Registry` llama `metrics()` una sola vez, en su constructor. Si
    el `Registry` se arma antes de que el sidecar entregue esa primera
    muestra (`client.wait_ready()` sin esperar), los `disk.temp.N` quedan
    afuera para siempre en esa corrida: no hay revalidacion posterior.
    """

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
