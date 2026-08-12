"""Punto de entrada de linea de comandos: python -m vmaxpanel

Corre el motor en primer plano. La app de bandeja (vmaxpanel.tray) es la otra
entrada: la misma maquinaria, manejada desde un menu en vez de la consola.
"""
import argparse
import sys
from pathlib import Path

from .engine import Engine, EngineConfig
from .layout import loader
from .logsetup import run_with_log
from .providers.setup import build_registry, build_registry_without_sensors
from .render.renderer import Renderer
from .transport.panel_link import PanelLink

HERE = Path(__file__).resolve().parent


def default_profile_path() -> Path:
    return HERE / "profiles" / "vitals.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vmaxpanel")
    ap.add_argument("--profile", type=Path, default=default_profile_path())
    ap.add_argument("--save", type=Path, help="renderiza un PNG y sale, sin tocar el panel")
    ap.add_argument("--port", help="COM del panel; por defecto se autodetecta")
    ap.add_argument("--once", action="store_true", help="manda un solo frame")
    ap.add_argument("--no-sensors", action="store_true",
                    help="no lanza el sidecar (util para probar layouts)")
    ap.add_argument("--log", type=Path,
                    help="ademas de la consola, escribe todo a este archivo "
                         "(necesario cuando corre con pythonw.exe, que no tiene "
                         "consola donde imprimir)")
    a = ap.parse_args(argv)

    return run_with_log(a.log, lambda: _run(a))


def _run(a) -> int:
    store = loader.ProfileStore(a.profile)
    errors = store.load_now()
    if errors:
        for e in errors:
            print(f"layout: {e}", file=sys.stderr)
        return 2

    if a.no_sensors:
        registry, client = build_registry_without_sensors()
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
