"""Punto de entrada de fase 1: python -m vmaxpanel

En la fase 3 esto queda debajo del servicio; por ahora reemplaza a
daemon/panel.py sin dejar de arrancarse a mano.
"""
import argparse
import sys
import traceback
from pathlib import Path

from .engine import Engine, EngineConfig
from .layout import loader
from .providers.msr import MsrProvider
from .providers.psutil_provider import PsutilProvider
from .providers.registry import Registry
from .providers.sidecar import SidecarClient
from .providers.sidecar_providers import (Gsa1Provider, LhmProvider, PdhProvider,
                                          SmbiosProvider)
from .render.renderer import Renderer
from .transport.panel_link import PanelLink

HERE = Path(__file__).resolve().parent
SIDECAR = HERE / "sensors.ps1"


def default_profile_path() -> Path:
    return HERE / "profiles" / "vitals.json"


class _Tee:
    """Escribe en el archivo de log y, si hay consola, tambien en ella.

    La tarea programada corre `pythonw.exe`, que no tiene consola: sin log,
    un motor que muere al logon deja el panel negro y ningun rastro de por
    que. Con pythonw, sys.stdout/sys.stderr pueden ser None -- de ahi el
    chequeo de `stream` antes de escribir.

    flush en cada linea a proposito: lo que se quiere leer es justamente lo
    ultimo que se escribio antes de morir, y un buffer sin vaciar se lo
    lleva puesto.
    """

    def __init__(self, fh, stream):
        self._fh, self._stream = fh, stream

    def write(self, s):
        self._fh.write(s)
        self._fh.flush()
        if self._stream is not None:
            try:
                self._stream.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        self._fh.flush()
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass


def build_registry(sidecar_script=SIDECAR, warmup=25.0):
    client = SidecarClient(sidecar_script).start()
    if not client.wait_ready(warmup):
        print("aviso: el sidecar no entrego datos; los sensores de hardware "
              "van a quedar no disponibles", file=sys.stderr)
    return Registry([PsutilProvider(), Gsa1Provider(client), PdhProvider(client),
                     LhmProvider(client), SmbiosProvider(client),
                     MsrProvider()]), client


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

    if a.log is None:
        return _run(a)

    a.log.parent.mkdir(parents=True, exist_ok=True)
    with open(a.log, "a", encoding="utf-8", errors="replace") as fh:
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Tee(fh, saved_out), _Tee(fh, saved_err)
        try:
            return _run(a)
        except BaseException:
            # El traceback lo imprime el interprete DESPUES de que main()
            # termina, o sea despues de que el finally restaure stderr y
            # cierre el archivo: para entonces ya no hay donde escribirlo.
            # Se emite aca, mientras stderr todavia es el Tee.
            traceback.print_exc(file=sys.stderr)
            raise
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err


def _run(a) -> int:
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
