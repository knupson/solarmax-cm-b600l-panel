"""Last-resort explanation for `cpu.power` and `cpu.fan`.

It serves nothing. Both metrics come from LibreHardwareMonitor -- package power
from RAPL through `cpulhm`, fan RPM from the board SuperIO through `mobo` -- and
this provider sits below them in the priority order, so it only ever speaks on a
machine where that DLL is absent. Its job is to say WHY the reading is missing
instead of leaving the editor showing "--" for no stated reason.

This module used to claim that WinRing0 was blocked by Windows and that MSR
access was therefore impossible. Both halves were wrong: the driver was loading
(under the service name `R0powershell`, which is why looking for "WinRing0" found
nothing), and the readings work. LibreHardwareMonitor 0.9.5+ uses PawnIO instead
and needs no blocklisted driver at all.
"""
from .base import Provider


class MsrProvider(Provider):
    id = "msr"

    def probe(self) -> bool:
        self.unavailable_reason = (
            "comes from the optional LibreHardwareMonitor DLL, which is not "
            "installed: see `--diagnose`")
        return False

    def metrics(self) -> set[str]:
        return {"cpu.power", "cpu.fan"}

    def read(self):
        return {}
