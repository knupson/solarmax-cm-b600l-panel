"""Providers que leen del mismo SidecarClient, cada uno su namespace."""
from ..metrics import MetricSpec, group_for, short_cpu_name, spec_for
from .base import Provider
from .sidecar import STALE_AFTER


class _SidecarProvider(Provider):
    namespace = "?"
    served: set[str] = set()
    reason = "the sidecar did not report this capability"

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
            raise RuntimeError(f"the sidecar has delivered no data for more "
                               f"de {STALE_AFTER:.0f} s")
        if not self._c.caps().get(self.namespace, False):
            raise RuntimeError(self.reason)
        return self._c.namespace(self.namespace)


class Gsa1Provider(_SidecarProvider):
    id = "gsa1"
    namespace = "gsa1"
    served = {"cpu.temp", "cpu.vrm_temp", "cpu.vcore"}
    reason = ("needs a Gigabyte board with the GSA1 ACPI-WMI interface "
              "(class GSA1_ACPIMethod)")


class PdhProvider(_SidecarProvider):
    """Clock real de CPU y modelo, con el nombre corto derivado aca.

    `cpu.name_short` se calcula en Python y no en sensors.ps1 a proposito: la
    regla de que sacar de "12th Gen Intel(R) Core(TM) i5-12400F" tiene tests
    (ver metrics.short_cpu_name) y no hay por que duplicarla en PowerShell,
    donde ademas quedaria sin cobertura.
    """

    id = "pdh"
    namespace = "pdh"
    served = {"cpu.clock", "cpu.name", "cpu.name_short"}
    reason = "could not read the PDH counter % Processor Performance"

    def read(self):
        muestra = super().read()
        nombre = muestra.get("cpu.name")
        if nombre is not None:
            muestra["cpu.name_short"] = short_cpu_name(nombre)
        return muestra


class SmbiosProvider(_SidecarProvider):
    """Velocidad real de la RAM, de Win32_PhysicalMemory.

    Estaba horneada en el perfil como un label con el texto "6000" hasta que
    una actualizacion de BIOS reseteo el XMP y la maquina paso a 5600: el
    panel siguio mostrando 6000. Un dato de configuracion tambien puede
    cambiar abajo tuyo, asi que se lee en vez de escribirse a mano.

    El sidecar lo consulta una sola vez al arrancar -- SMBIOS no cambia
    mientras Windows corre -- pero igual pasa por el mismo gate de frescura
    que el resto: si el sidecar se muere, este valor deja de servirse como
    cualquier otro, en vez de quedar congelado en pantalla.
    """

    id = "smbios"
    namespace = "smbios"
    served = {"mem.speed"}
    reason = "could not read Win32_PhysicalMemory"


class _DinamicoPorInstancia(_SidecarProvider):
    """Base de los providers cuyo conjunto de metricas se descubre solo.

    Cuantos nucleos, cuantos fans y cuantas temperaturas de placa hay no se
    sabe hasta ver la primera muestra, asi que `served` se lee del namespace en
    vez de estar escrito a mano. Mismo patron que LhmProvider con los discos, y
    con la misma trampa: `Registry` llama a `metrics()` una sola vez, en su
    constructor, asi que lo que no este en esa primera muestra no aparece en
    toda la corrida.
    """

    @property
    def served(self):
        return set(self._c.namespace(self.namespace))

    def metrics(self) -> set[str]:
        return set(self.served)

    def catalog(self) -> dict:
        """Etiqueta amigable por metrica, tomada de la familia de metrics.py."""
        cat = {}
        for mid in self.served:
            base = spec_for(mid)
            if base is not None:
                cat[mid] = base
        return cat

    def groups(self) -> dict:
        return {mid: group_for(mid) for mid in self.served}


class CpuLhmProvider(_DinamicoPorInstancia):
    """CPU por LibreHardwareMonitor: package power y por nucleo.

    `cpu.power` estaba documentado en este proyecto como imposible de leer,
    porque WinRing0 esta en la blocklist de drivers de Windows y sin MSR no hay
    RAPL. La conclusion era erronea para el DLL que traemos: LHM 0.9.3.0 lo lee
    sin cargar ningun driver -- verificado listando los servicios con el objeto
    abierto -- y el sidecar simplemente nunca habia prendido `IsCpuEnabled`.
    Comprobado ademas contra carga real: 11 W en reposo, 46 W al 43%.
    """

    id = "cpulhm"
    namespace = "cpulhm"
    reason = "LibreHardwareMonitor exposed no CPU sensors"


class MoboProvider(_DinamicoPorInstancia):
    """Placa: fans y temperaturas del SuperIO (aca, un ITE IT8689E).

    Los `fan.N.rpm` se exponen todos porque el mapeo conector -> ventilador
    depende de la placa. `cpu.fan` lo agrega el sidecar desde el fan 1, que es
    CPU_FAN en las placas Gigabyte y en esta maquina el unico que gira con el
    equipo encendido.
    """

    id = "mobo"
    namespace = "mobo"
    reason = "could not read the board SuperIO"

    def catalog(self) -> dict:
        cat = super().catalog()
        # cpu.fan no es de una familia por instancia: su etiqueta sale de
        # METRICS, pero conviene que diga de donde viene.
        if "cpu.fan" in cat:
            base = cat["cpu.fan"]
            cat["cpu.fan"] = MetricSpec(base.id, "CPU fan (header 1)",
                                        base.unit, base.kind, base.min, base.max)
        return cat


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
    reason = ("could not open LibreHardwareMonitor "
              "(LibreHardwareMonitorLib.dll or HidSharp.dll is missing beside it)")

    @property
    def served(self):
        disks = {k for k in self._c.namespace("lhm") if k.startswith("disk.temp.")}
        return self._FIXED | disks

    def metrics(self) -> set[str]:
        return set(self.served)
