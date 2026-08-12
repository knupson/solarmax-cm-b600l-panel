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

    def catalog(self) -> dict:
        """id -> MetricSpec con la etiqueta que ve el usuario.

        Opcional. Sirve para las metricas cuyo nombre lindo no se puede deducir
        del id porque depende del hardware: `vol.D.free` no sabe que la D se
        llama "JUEGOS", ni `fan.1.rpm` a que conector corresponde. El editor
        prefiere esta etiqueta sobre la generica de metrics.spec_for().
        """
        return {}

    def groups(self) -> dict:
        """id -> nombre del dispositivo al que pertenece.

        Opcional, para que el editor agrupe la lista de metricas por
        dispositivo en vez de mostrar cien ids sueltos.
        """
        return {}

    def close(self) -> None:
        pass
