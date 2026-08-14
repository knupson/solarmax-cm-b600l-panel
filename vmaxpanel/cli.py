"""Command-line entry point: python -m vmaxpanel

Runs the engine in the foreground. The tray app (vmaxpanel.tray) is the other
entry point: the same machinery, driven from a menu instead of the console.

Every Spanish flag name is kept as an alias of its English one. They were the
original names, they are in installed scheduled tasks and in whatever scripts
people already wrote, and breaking those to make the help text prettier is not a
trade worth making. `dest` is pinned so renaming an option never silently renames
the attribute the rest of this module reads.
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

# The Spanish values are what bundle.import_bundle speaks; the English ones are
# aliases accepted on the command line and translated here, so the flag reads in
# English without a rename reaching into the bundle code.
SI_EXISTE = {"fail": "fallar", "rename": "renombrar", "overwrite": "pisar",
             "fallar": "fallar", "renombrar": "renombrar", "pisar": "pisar"}

# Only the English spellings are ADVERTISED. Passing choices=tuple(SI_EXISTE) put
# all six in the usage line of every --help, where they read as six different
# options rather than three with aliases. The Spanish ones keep working.
SI_EXISTE_PUBLICAS = ("fail", "rename", "overwrite")


def _si_existe(valor):
    """argparse type= for --if-exists: accepts either spelling, shows three."""
    if valor not in SI_EXISTE:
        opciones = ", ".join(SI_EXISTE_PUBLICAS)
        raise argparse.ArgumentTypeError(f"{valor!r}: expected one of {opciones}")
    return valor


def default_profile_path() -> Path:
    return HERE / "profiles" / "vitals.json"


def profiles_dir() -> Path:
    """Where the profiles live. A function, not a constant, so it can be replaced."""
    return HERE / "profiles"


def assets_dir() -> Path:
    return HERE / "assets"


def status_path() -> Path:
    """Where the process driving the panel publishes its status.

    Next to the log, and for the same reason: it is where the user already looks
    when something is wrong, and it has to be the same place for the writer and
    the reader without anybody configuring anything.
    """
    return HERE.parent / "vmaxpanel-estado.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vmaxpanel")
    ap.add_argument("--profile", type=Path, default=default_profile_path(),
                    metavar="FILE",
                    help="the layout to use (default: the Vitals profile)")
    ap.add_argument("--save", type=Path,
                    help="render a PNG and exit, without touching the panel")
    ap.add_argument("--port", help="the panel's COM port; autodetected by default")
    ap.add_argument("--once", action="store_true", help="send a single frame")
    ap.add_argument("--no-sensors", action="store_true",
                    help="do not launch the sidecar (useful for testing layouts)")
    ap.add_argument("--log", type=Path,
                    help="write everything to this file as well as to the console "
                         "(required when running under pythonw.exe, which has no "
                         "console to print to)")
    ap.add_argument("--diagnose", "--diagnostico", dest="diagnostico",
                    action="store_true",
                    help="check dependencies, sensors, profile and panel, then "
                         "exit without changing anything")
    ap.add_argument("--install", "--instalar", dest="instalar", action="store_true",
                    help="check everything, then register the task that starts the "
                         "tray at logon")
    ap.add_argument("--uninstall", "--desinstalar", dest="desinstalar",
                    action="store_true",
                    help="remove that task; the panel stops starting on its own")
    ap.add_argument("--stop", "--parar", dest="parar", action="store_true",
                    help="bring the panel down now: stop the task and kill the "
                         "tray, the engine and the sensor sidecar")
    ap.add_argument("--status", "--estado", dest="estado", action="store_true",
                    help="say whether the panel is drawing right now, by reading "
                         "what the process driving it publishes")
    ap.add_argument("--export", "--exportar", dest="exportar", type=Path,
                    metavar="FILE",
                    help="save the profile and its assets into a single file "
                         f"({bundle.EXT}) to share or back up")
    ap.add_argument("--import", "--importar", dest="importar", type=Path,
                    metavar="FILE",
                    help="install a profile exported with --export")
    ap.add_argument("--if-exists", "--si-existe", dest="si_existe",
                    type=_si_existe, default="fail",
                    metavar="{%s}" % ",".join(SI_EXISTE_PUBLICAS),
                    help="what to do on import when a profile of that name already "
                         "exists (default: fail and change nothing)")
    a = ap.parse_args(argv)

    # Before run_with_log: these all print to the console and exit. Writing the
    # diagnostic to a log file instead of to the screen would be the exact
    # opposite of what somebody saying "it does not work" needs.
    if a.parar:
        return _reportar(install.parar())
    if a.desinstalar:
        return _reportar(install.desinstalar())
    if a.instalar:
        print(f"Installing VMax Panel with profile {a.profile}")
        return _reportar(install.instalar(a.profile, log=a.log or _log_por_defecto(),
                                          port=a.port))
    if a.estado:
        leido = status.StatusFile(status_path()).read()
        print(status.describe(leido))
        # 1 and not 2: this is neither a usage error nor a failed command, it is
        # the answer "it is not running" -- which a script can tell apart by code.
        return 0 if leido and leido.get("running") else 1
    if a.exportar:
        return _exportar(a.profile, a.exportar)
    if a.importar:
        return _importar(a.importar, SI_EXISTE[a.si_existe])
    if a.diagnostico:
        checks = install.diagnosticar(a.profile, a.port)
        for c in checks:
            print(f"  [{c.marca:>8}] {c.nombre}: {c.detalle}")
        return 2 if install.bloquea(checks) else 0

    return run_with_log(a.log, lambda: _run(a))


def _log_por_defecto() -> Path:
    """Where the tray writes when the scheduled task starts it.

    Beside the repo and not in %TEMP%: the task runs under pythonw, with no
    console, so this file is the ONLY place the reason the panel failed to start
    is recorded. It has to be somewhere the user will find it.
    """
    return HERE.parent / "vmaxpanel.log"


def _exportar(perfil, destino) -> int:
    destino = Path(destino)
    if destino.exists():
        # No --if-exists for export, on purpose: the previous bundle may be the
        # very one the user already sent to somebody, and overwriting it silently
        # cannot be undone. Choosing another name costs less than recovering it.
        print(f"{destino} already exists: pick another name or delete it first.")
        return 2
    try:
        info = bundle.export_profile(perfil, destino, assets_dir())
    except bundle.BundleError as e:
        print(str(e))
        return 2
    kb = destino.stat().st_size / 1024
    print(f"exported: {destino} ({kb:.0f} KB)")
    print(f"  profile: {Path(perfil).name}")
    print(f"  assets:  {', '.join(info['assets']) or 'none'}")
    print(f"  fonts:   {', '.join(info['fonts'])}")
    if info["faltantes"]:
        print(f"  CAREFUL, these were missing and are not in the bundle: "
              f"{', '.join(info['faltantes'])}")
    # Fonts do not travel, and that is not an oversight: they are Microsoft's.
    # Said here so nobody is surprised on the other side.
    print("  (fonts are not packaged: they are requested by family and are "
          "present on any Windows)")
    return 0


def _importar(origen, si_existe) -> int:
    try:
        info = bundle.import_bundle(origen, profiles_dir(), assets_dir(),
                                    si_existe=si_existe)
    except bundle.BundleError as e:
        print(str(e))
        return 2
    print(f"imported: {info['profile']}")
    if info["assets"]:
        print(f"  assets: {', '.join(info['assets'])}")
    if info["fuentes_faltantes"]:
        print(f"  fonts NOT present on this machine: "
              f"{', '.join(info['fuentes_faltantes'])}")
        print("  the panel will substitute another one: the layout looks "
              "different but it works.")
    print(f"  to use it: pick it from the tray menu, or "
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
                print(f"warning: {w}", file=sys.stderr)
            print("saved", a.save)
            return 0

        cfg = EngineConfig(profile_path=a.profile, max_iterations=1 if a.once else None)
        eng = Engine(store, registry, cfg,
                     link_factory=lambda: PanelLink.autodetect(a.port))
        print(f"profile {store.current.name!r}; "
              f"unavailable metrics: {sorted(registry.unavailable())}")
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
