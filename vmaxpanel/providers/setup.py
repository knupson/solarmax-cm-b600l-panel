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
from .sidecar_providers import (Gsa1Provider, LhmProvider, PdhProvider,
                                SmbiosProvider)

SIDECAR = Path(__file__).resolve().parent.parent / "sensors.ps1"


def build_registry(sidecar_script=SIDECAR, warmup=25.0):
    """(registry, client). El client puede ser None: el caller cierra los dos."""
    client = SidecarClient(sidecar_script).start()
    if not client.wait_ready(warmup):
        print("aviso: el sidecar no entrego datos; los sensores de hardware "
              "van a quedar no disponibles", file=sys.stderr)
    return Registry([PsutilProvider(), Gsa1Provider(client), PdhProvider(client),
                     LhmProvider(client), SmbiosProvider(client),
                     MsrProvider()]), client


def build_registry_without_sensors():
    """Solo psutil: para previsualizar layouts sin levantar el sidecar."""
    return Registry([PsutilProvider()]), None
