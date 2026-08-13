"""Armado del registry real de esta maquina.

Vive aparte del CLI porque lo necesitan tres entradas distintas -- el CLI, la
app de bandeja y el preview del editor -- y ninguna tiene por que importar a
las otras.
"""
import sys
from pathlib import Path

from .msr import MsrProvider
from .psutil_provider import PsutilProvider
from .registry import Registry
from .sidecar import SidecarClient
from .sidecar_providers import (CpuLhmProvider, Gsa1Provider, LhmProvider,
                                MoboProvider, PdhProvider, SmbiosProvider)
from .wmi_provider import WmiProvider

SIDECAR = Path(__file__).resolve().parent.parent / "sensors.ps1"


def build_registry(sidecar_script=SIDECAR, warmup=25.0):
    """(registry, client). El client puede ser None: el caller cierra los dos."""
    client = SidecarClient(sidecar_script).start()
    if not client.wait_ready(warmup):
        print("warning: the sidecar delivered no data; hardware sensors will "
              "be unavailable", file=sys.stderr)
    return Registry([PsutilProvider(), Gsa1Provider(client), PdhProvider(client),
                     LhmProvider(client), SmbiosProvider(client),
                     CpuLhmProvider(client), MoboProvider(client), WmiProvider(),
                     MsrProvider()]), client


def build_registry_without_sensors():
    """Sin sidecar: psutil y WMI, que no necesitan DLLs ni elevacion.

    Se usa para previsualizar layouts. WMI entra porque el espacio en disco es
    justamente uno de los datos que se quiere ver al disenar, y su consulta no
    depende de LibreHardwareMonitor.
    """
    return Registry([PsutilProvider(), WmiProvider()]), None
