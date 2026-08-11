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
            try:
                self._proc = self._spawn()
                for line in self._proc.stdout:
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
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
