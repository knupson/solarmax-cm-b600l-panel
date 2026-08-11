"""Package power y fan RPM de CPU: necesitan MSR via driver ring0.

En esta maquina WinRing0 esta bloqueado: StartService devuelve 0xE1
(ERROR_VIRUS_INFECTED) porque el driver esta en la blocklist de drivers
vulnerables de Windows. No se intenta cargarlo.

El provider existe igual para que el editor pueda explicar POR QUE la metrica
no esta, en vez de mostrar "--" sin motivo. En una maquina donde el driver
cargue, aca es donde se implementa la lectura.
"""
from .base import Provider


class MsrProvider(Provider):
    id = "msr"

    def probe(self) -> bool:
        self.unavailable_reason = (
            "requiere acceso a MSR por driver ring0 (WinRing0), bloqueado por "
            "la blocklist de drivers vulnerables de Windows")
        return False

    def metrics(self) -> set[str]:
        return {"cpu.power", "cpu.fan"}

    def read(self):
        return {}
