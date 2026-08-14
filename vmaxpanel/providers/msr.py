"""Readings that need MSR access through a ring0 driver.

WinRing0 is blocked on Windows: StartService returns 0xE1
(ERROR_VIRUS_INFECTED) because the driver is on the vulnerable-driver
blocklist. No attempt is made to load it.

The provider exists anyway so the editor can explain WHY a metric is absent,
instead of showing "--" for no stated reason. On a machine where such a driver
did load, this is where the reading would be implemented.

Note that CPU package power and fan RPM, which this module was originally
written for, are now served without any ring0 driver at all: LibreHardwareMonitor
reads RAPL directly and the fans come off the board SuperIO.
"""
from .base import Provider


class MsrProvider(Provider):
    id = "msr"

    def probe(self) -> bool:
        self.unavailable_reason = (
            "needs MSR access through a ring0 driver (WinRing0), blocked by "
            "the Windows vulnerable-driver blocklist")
        return False

    def metrics(self) -> set[str]:
        return {"cpu.power", "cpu.fan"}

    def read(self):
        return {}
