"""Building the real registry for this machine.

It lives apart from the CLI because three separate entry points need it -- the
CLI, the tray app and the editor's preview -- and none of them has any reason to
import the others.
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
    """(registry, client). The client can be None: the caller closes both."""
    client = SidecarClient(sidecar_script).start()
    if not client.wait_ready(warmup):
        print("warning: the sidecar delivered no data; hardware sensors will "
              "be unavailable", file=sys.stderr)
    return Registry([PsutilProvider(), Gsa1Provider(client), PdhProvider(client),
                     LhmProvider(client), SmbiosProvider(client),
                     CpuLhmProvider(client), MoboProvider(client), WmiProvider(),
                     MsrProvider()]), client


def build_registry_without_sensors():
    """No sidecar: psutil and WMI, which need neither DLLs nor elevation.

    Used for previewing layouts. WMI is included because disk space is precisely
    one of the readings you want to see while designing, and querying it does not
    depend on LibreHardwareMonitor.
    """
    return Registry([PsutilProvider(), WmiProvider()]), None
