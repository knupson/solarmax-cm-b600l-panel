"""Contrato de un provider de sensores.

Un provider declara que ids canonicos sirve y si existe en esta maquina.
SOLO lecturas: ningun provider invoca metodos de escritura de hardware.
"""
from abc import ABC, abstractmethod


class Provider(ABC):
    id: str = "?"
    unavailable_reason: str | None = None

    @abstractmethod
    def probe(self) -> bool:
        """True si este provider funciona en esta maquina.

        Si devuelve False debe dejar `unavailable_reason` con el motivo en
        lenguaje llano: es lo que el editor le muestra al usuario.
        """

    @abstractmethod
    def metrics(self) -> set[str]:
        """Ids canonicos que este provider sirve."""

    @abstractmethod
    def read(self) -> dict[str, float | str | None]:
        """Ultima muestra. Las claves deben ser subconjunto de metrics()."""

    def close(self) -> None:
        pass
