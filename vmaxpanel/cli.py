"""Punto de entrada de linea de comandos: python -m vmaxpanel

Corre el motor en primer plano. La app de bandeja (vmaxpanel.tray) es la otra
entrada: la misma maquinaria, manejada desde un menu en vez de la consola.
"""
import argparse
import sys
from pathlib import Path

from . import bundle, install, status
from .engine import Engine, EngineConfig
from .layout import loader
from .logsetup import run_with_log
from .providers.setup import build_registry, build_registry_without_sensors
from .render.renderer import Renderer
from .transport.panel_link import PanelLink

HERE = Path(__file__).resolve().parent


def default_profile_path() -> Path:
    return HERE / "profiles" / "vitals.json"


def profiles_dir() -> Path:
    """Donde viven los perfiles. Funcion y no constante para poder sustituirla."""
    return HERE / "profiles"


def assets_dir() -> Path:
    return HERE / "assets"


def status_path() -> Path:
    """Donde el proceso que maneja el panel publica su estado.

    Al lado del log y por el mismo motivo: es donde el usuario ya va a buscar
    cuando algo no anda, y tiene que ser el mismo lugar para el que escribe y el
    que lee sin que nadie configure nada.
    """
    return HERE.parent / "vmaxpanel-estado.json"


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
    ap.add_argument("--diagnostico", action="store_true",
                    help="revisa dependencias, sensores, perfil y panel, y sale "
                         "sin tocar nada")
    ap.add_argument("--instalar", action="store_true",
                    help="revisa todo y registra la tarea que arranca la bandeja "
                         "al iniciar sesion")
    ap.add_argument("--desinstalar", action="store_true",
                    help="borra esa tarea; el panel deja de arrancar solo")
    ap.add_argument("--estado", action="store_true",
                    help="dice si el panel esta andando ahora, leyendo lo que "
                         "publica el proceso que lo maneja")
    ap.add_argument("--exportar", type=Path, metavar="ARCHIVO",
                    help="guarda el perfil y sus assets en un solo archivo "
                         f"({bundle.EXT}) para compartirlo o respaldarlo")
    ap.add_argument("--importar", type=Path, metavar="ARCHIVO",
                    help="instala un perfil exportado con --exportar")
    ap.add_argument("--si-existe", choices=("fallar", "renombrar", "pisar"),
                    default="fallar",
                    help="que hacer al importar si ya hay un perfil con ese "
                         "nombre (por defecto: fallar y no tocar nada)")
    a = ap.parse_args(argv)

    # Antes del run_with_log: estos tres salen por consola y terminan. Escribir
    # el diagnostico a un log en vez de a la pantalla seria justo lo contrario de
    # lo que se le pide a alguien que dice "no anda".
    if a.desinstalar:
        return _reportar(install.desinstalar())
    if a.instalar:
        print(f"Instalando VMax Panel con el perfil {a.profile}")
        return _reportar(install.instalar(a.profile, log=a.log or _log_por_defecto(),
                                          port=a.port))
    if a.estado:
        leido = status.StatusFile(status_path()).read()
        print(status.describe(leido))
        # 1 y no 2: no es un error de uso ni una falla del comando, es la respuesta
        # "no esta corriendo" -- que un script pueda distinguir con el codigo.
        return 0 if leido and leido.get("running") else 1
    if a.exportar:
        return _exportar(a.profile, a.exportar)
    if a.importar:
        return _importar(a.importar, a.si_existe)
    if a.diagnostico:
        checks = install.diagnosticar(a.profile, a.port)
        for c in checks:
            print(f"  [{c.marca:>8}] {c.nombre}: {c.detalle}")
        return 2 if install.bloquea(checks) else 0

    return run_with_log(a.log, lambda: _run(a))


def _log_por_defecto() -> Path:
    """Donde escribe la bandeja cuando la levanta la tarea.

    Al lado del repo y no en %TEMP%: la tarea corre con pythonw, sin consola, asi
    que este archivo es el UNICO lugar donde queda el motivo de que el panel no
    haya arrancado. Tiene que estar donde el usuario lo encuentre.
    """
    return HERE.parent / "vmaxpanel.log"


def _exportar(perfil, destino) -> int:
    destino = Path(destino)
    if destino.exists():
        # Sin --si-existe para exportar a proposito: el bundle anterior puede ser
        # justo el que el usuario ya le mando a alguien, y pisarlo en silencio no
        # tiene vuelta atras. Cambiar el nombre cuesta menos que recuperarlo.
        print(f"{destino} ya existe: elegi otro nombre o borralo primero.")
        return 2
    try:
        info = bundle.export_profile(perfil, destino, assets_dir())
    except bundle.BundleError as e:
        print(str(e))
        return 2
    kb = destino.stat().st_size / 1024
    print(f"exportado: {destino} ({kb:.0f} KB)")
    print(f"  perfil:  {Path(perfil).name}")
    print(f"  assets:  {', '.join(info['assets']) or 'ninguno'}")
    print(f"  fuentes: {', '.join(info['fonts'])}")
    if info["faltantes"]:
        print(f"  OJO, no estaban y no van en el bundle: "
              f"{', '.join(info['faltantes'])}")
    # Las fuentes no viajan y eso no es un olvido: son de Microsoft. Se dice aca
    # para que nadie se sorprenda del otro lado.
    print("  (las fuentes no se empaquetan: se piden por familia y en cualquier "
          "Windows estan)")
    return 0


def _importar(origen, si_existe) -> int:
    try:
        info = bundle.import_bundle(origen, profiles_dir(), assets_dir(),
                                    si_existe=si_existe)
    except bundle.BundleError as e:
        print(str(e))
        return 2
    print(f"importado: {info['profile']}")
    if info["assets"]:
        print(f"  assets:  {', '.join(info['assets'])}")
    if info["fuentes_faltantes"]:
        print(f"  fuentes que NO estan en esta maquina: "
              f"{', '.join(info['fuentes_faltantes'])}")
        print("  el panel va a usar una de reemplazo: el layout se ve distinto "
              "pero funciona.")
    print(f"  para usarlo: elegilo en el menu de la bandeja, o "
          f"--profile {info['profile']}")
    return 0


def _reportar(resultado) -> int:
    code, lineas = resultado
    for l in lineas:
        print(l)
    return code


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
