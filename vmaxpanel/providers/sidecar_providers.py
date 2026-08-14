"""Providers reading from the same SidecarClient, each its own namespace."""
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
        """Raises instead of returning stale data.

        Registry.read() turns the exception into a degraded metric, with the reason
        visible in state()["unavailable"], and serves it again on its own when the
        next read works. Returning the last namespace received would be worse than
        showing nothing: a wedged sensors.ps1 -- blocked in a Get-Counter or a CIM
        call -- leaves the panel painting a cpu.temp from hours ago as if it were
        current, with nothing in the status saying so.

        Both conditions are checked on every pass on purpose. probe() runs once, in
        Registry.__init__, so it used to be the only place that looked at caps: a
        source failing halfway through a run still counted as available, contrary to
        what is documented in
        sensors.ps1.
        """
        if not self._c.fresh:
            raise RuntimeError(f"the sidecar has delivered no data for more "
                               f"than {STALE_AFTER:.0f} s")
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
    """The real CPU clock and model, with the short name derived here.

    `cpu.name_short` is computed in Python and not in sensors.ps1 on purpose: the
    rule for what to strip out of "12th Gen Intel(R) Core(TM) i5-12400F" has tests
    (see metrics.short_cpu_name) and there is no reason to duplicate it in
    PowerShell, where it would also go uncovered.
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
    """The real RAM speed, from Win32_PhysicalMemory.

    It used to be baked into the profile as a label reading "6000" until a BIOS
    update reset the XMP profile and the machine dropped to 5600: the panel went on
    showing 6000. A configuration value can change underneath you too, so it is
    read rather than written by hand.

    The sidecar queries it once at start-up -- SMBIOS does not change while Windows
    runs -- but it still goes through the same freshness gate as everything else:
    if the sidecar dies, this value stops being served like any other, instead of
    staying frozen on screen.
    """

    id = "smbios"
    namespace = "smbios"
    served = {"mem.speed", "mb.name"}
    reason = "could not read Win32_PhysicalMemory"


class _DinamicoPorInstancia(_SidecarProvider):
    """Base for providers whose metric set discovers itself.

    How many cores, how many fans and how many board temperatures there are is not
    known until the first sample arrives, so `served` is read from the namespace
    rather than written by hand. Same pattern as LhmProvider with the disks, and
    with the same trap: `Registry` calls `metrics()` once, in its constructor, so
    anything absent from that first sample does not appear for the whole run.
    """

    @property
    def served(self):
        return set(self._c.namespace(self.namespace))

    def metrics(self) -> set[str]:
        return set(self.served)

    def catalog(self) -> dict:
        """A friendly label per metric, taken from the family in metrics.py."""
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

    `cpu.power` was documented in this project as impossible to read, because
    WinRing0 is on the Windows driver blocklist and without MSR there is no RAPL.
    That conclusion was wrong for this DLL: LHM 0.9.3.0 reads it without loading
    any driver -- verified by listing the services with the object open -- and the
    sidecar had simply never turned `IsCpuEnabled` on.
    Comprobado ademas contra carga real: 11 W en reposo, 46 W al 43%.
    """

    id = "cpulhm"
    namespace = "cpulhm"
    reason = "LibreHardwareMonitor exposed no CPU sensors"


class MoboProvider(_DinamicoPorInstancia):
    """The board: fans and temperatures from the SuperIO (an ITE IT8689E here).

    Every `fan.N.rpm` is exposed because the header -> fan mapping depends on the
    board. `cpu.fan` is added by the sidecar from fan 1, which is CPU_FAN on
    Gigabyte boards and, on the machine this was written against, the only one that
    spins with the
    equipo encendido.
    """

    id = "mobo"
    namespace = "mobo"
    reason = "could not read the board SuperIO"

    def catalog(self) -> dict:
        cat = super().catalog()
        # cpu.fan does not belong to a per-instance family: its label comes from
        # METRICS, but it is worth saying where it comes from.
        if "cpu.fan" in cat:
            base = cat["cpu.fan"]
            cat["cpu.fan"] = MetricSpec(base.id, "CPU fan (header 1)",
                                        base.unit, base.kind, base.min, base.max)
        return cat


class LhmProvider(_SidecarProvider):
    """GPU and SSD temperatures. The disk ids are discovered from the sample.

    Ordering hazard: `served` reads the first sample that arrived from the sidecar.
    `Registry` calls `metrics()` once, in its constructor. If the `Registry` is
    built before the sidecar delivers that first sample (`client.wait_ready()`
    without waiting), the `disk.temp.N` ids are left out for that entire run: there
    is no later revalidation.
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
