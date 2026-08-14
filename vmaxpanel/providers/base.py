"""Contrato de un provider de sensores.

A provider declares which canonical ids it serves and whether it exists on this
machine. READS ONLY: no provider invokes a hardware write method.
"""
from abc import ABC, abstractmethod


class Provider(ABC):
    id: str = "?"
    unavailable_reason: str | None = None

    @abstractmethod
    def probe(self) -> bool:
        """True if this provider works on this machine.

        When it returns False it must leave `unavailable_reason` set to the reason
        in plain language: that is what the editor shows the user.
        """

    @abstractmethod
    def metrics(self) -> set[str]:
        """The canonical ids this provider serves."""

    @abstractmethod
    def read(self) -> dict[str, float | str | None]:
        """The latest sample. Its keys must be a subset of metrics()."""

    def catalog(self) -> dict:
        """id -> MetricSpec carrying the label the user sees.

        Optional. It exists for metrics whose friendly name cannot be derived from
        the id because it depends on the hardware: `vol.D.free` does not know that
        D is called "GAMES", nor `fan.1.rpm` which header it belongs to. The editor
        prefers this label over the generic one from metrics.spec_for().
        """
        return {}

    def groups(self) -> dict:
        """id -> the name of the device it belongs to.

        Optional, so the editor can group the metric list by
        dispositivo en vez de mostrar cien ids sueltos.
        """
        return {}

    def close(self) -> None:
        pass
