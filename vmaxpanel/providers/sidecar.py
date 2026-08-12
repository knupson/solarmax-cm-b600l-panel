"""Maneja el proceso PowerShell que lee GSA1, PDH y LibreHardwareMonitor.

Un solo proceso alimenta tres providers. Si muere, se relanza con backoff y las
metricas de sus namespaces quedan sin refrescar hasta que vuelva.

Trampa conocida: un powershell.exe corriendo sensors.ps1 que sobrevive al
proceso padre se queda con LibreHardwareMonitorLib.dll tomado y bloquea mover o
borrar el directorio. Por eso close() termina el proceso siempre.
"""
import json
import os
import subprocess
import threading
import time

STALE_AFTER = 8.0
BACKOFF = [1.0, 2.0, 5.0, 10.0]


KILL_TIMEOUT = 5.0


def _kill(proc):
    """terminate() + wait(): pide la baja y ademas la espera.

    Cosechar el proceso es lo que libera el handle y el DLL que tenia
    cargado. Un wait() que se pasa del timeout no puede tumbar al llamador
    -- estamos justamente en el camino de apagado -- asi que se traga.
    """
    if proc is None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=KILL_TIMEOUT)
    except Exception:
        pass


def _default_spawn(script):
    def spawn():
        return subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", os.fspath(script)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return spawn


class SidecarClient:
    def __init__(self, script, spawn=None, restart=True):
        self._spawn = spawn or _default_spawn(script)
        self._restart = restart
        self._proc = None
        self._data: dict = {}
        self._last = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        attempt = 0
        while not self._stop.is_set():
            proc = None
            try:
                proc = self._spawn()
                with self._lock:
                    self._proc = proc
                if self._stop.is_set():
                    # close() puede haber corrido entre el chequeo del while y
                    # este spawn: mato el proceso anterior (o ninguno, si era
                    # el primero) y este recien nacido se queda sin dueno. Sin
                    # esta guarda queda un powershell vivo con
                    # LibreHardwareMonitorLib.dll tomado.
                    return
                for line in proc.stdout:
                    if self._stop.is_set():
                        break
                    line = line.strip()
                    if not line.startswith("{"):
                        continue        # el sidecar puede escribir avisos sueltos
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    with self._lock:
                        self._data = parsed
                        self._last = time.time()
                    self._ready.set()
                    attempt = 0
            except Exception:
                pass
            finally:
                # Cada vuelta cierra SU proceso. Sin esto, un respawn dejaba
                # el anterior sin terminar ni cosechar -- zombie con su
                # stdout abierto -- y el `return` de la guarda de arriba
                # dejaba vivo justamente al que acababa de crear.
                _kill(proc)
            if not self._restart or self._stop.is_set():
                return
            time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            attempt += 1

    def wait_ready(self, timeout=25.0) -> bool:
        return self._ready.wait(timeout)

    @property
    def fresh(self) -> bool:
        return time.time() - self._last < STALE_AFTER

    def caps(self) -> dict:
        with self._lock:
            return dict(self._data.get("caps") or {})

    def namespace(self, name: str) -> dict:
        with self._lock:
            return dict(self._data.get(name) or {})

    def close(self):
        """Baja el sidecar y espera a que muera de verdad.

        El wait() no es un lujo: terminate() solo pide la baja, y el caller
        que a continuacion borra o mueve el directorio todavia puede pegar
        contra el lock de LibreHardwareMonitorLib.dll -- la trampa que este
        modulo dice evitar. El proceso se toma bajo el lock pero se mata
        afuera, porque wait() bloquea y namespace()/caps() lo necesitan.
        """
        self._stop.set()
        with self._lock:
            proc = self._proc
        _kill(proc)
