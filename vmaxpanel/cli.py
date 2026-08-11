"""Punto de entrada de fase 1: python -m vmaxpanel

En la fase 3 esto queda debajo del servicio; por ahora reemplaza a
daemon/panel.py sin dejar de arrancarse a mano.
"""
import argparse
import sys
from pathlib import Path

from .engine import Engine, EngineConfig
from .layout import loader
from .providers.msr import MsrProvider
from .providers.psutil_provider import PsutilProvider
from .providers.registry import Registry
from .providers.sidecar import SidecarClient
from .providers.sidecar_providers import Gsa1Provider, LhmProvider, PdhProvider
from .render.renderer import Renderer
from .transport.panel_link import PanelLink

HERE = Path(__file__).resolve().parent
SIDECAR = HERE / "sensors.ps1"


def default_profile_path() -> Path:
    return HERE / "profiles" / "vitals.json"


def build_registry(sidecar_script=SIDECAR, warmup=25.0):
    client = SidecarClient(sidecar_script).start()
    if not client.wait_ready(warmup):
        print("aviso: el sidecar no entrego datos; los sensores de hardware "
              "van a quedar no disponibles", file=sys.stderr)
    return Registry([PsutilProvider(), Gsa1Provider(client), PdhProvider(client),
                     LhmProvider(client), MsrProvider()]), client


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vmaxpanel")
    ap.add_argument("--profile", type=Path, default=default_profile_path())
    ap.add_argument("--save", type=Path, help="renderiza un PNG y sale, sin tocar el panel")
    ap.add_argument("--port", help="COM del panel; por defecto se autodetecta")
    ap.add_argument("--once", action="store_true", help="manda un solo frame")
    ap.add_argument("--no-sensors", action="store_true",
                    help="no lanza el sidecar (util para probar layouts)")
    a = ap.parse_args(argv)

    store = loader.ProfileStore(a.profile)
    errors = store.load_now()
    if errors:
        for e in errors:
            print(f"layout: {e}", file=sys.stderr)
        return 2

    if a.no_sensors:
        registry, client = Registry([PsutilProvider()]), None
    else:
        registry, client = build_registry()

    try:
        if a.save:
            r = Renderer(store.current)
            r.frame(registry.read()).save(a.save)
            for w in r.warnings():
                print(f"aviso: {w}", file=sys.stderr)
            print("guardado", a.save)
            return 0

        cfg = EngineConfig(profile_path=a.profile, max_iterations=1 if a.once else None)
        eng = Engine(store, registry, cfg,
                     link_factory=lambda: PanelLink.autodetect(a.port))
        print(f"perfil {store.current.name!r}; "
              f"metricas no disponibles: {sorted(registry.unavailable())}")
        eng.run()
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        registry.close()
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
