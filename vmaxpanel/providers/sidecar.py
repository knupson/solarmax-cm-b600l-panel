"""Manages the PowerShell process that reads GSA1, PDH and LibreHardwareMonitor.

One process feeds several providers. If it dies it is relaunched with backoff, and
the metrics in its namespaces go unrefreshed until it comes back.

Known trap: a powershell.exe running sensors.ps1 that outlives its parent keeps
LibreHardwareMonitorLib.dll locked and blocks moving or deleting the directory.
That is why close() always terminates the process.
"""
import json
import os
import subprocess
import threading
import time

STALE_AFTER = 8.0
BACKOFF = [1.0, 2.0, 5.0, 10.0]


KILL_TIMEOUT = 5.0


# The named event the sidecar watches so it can shut down in an orderly way. It is
# per-process so two engines do not stop each other.
NOMBRE_EVENTO = f"vmaxpanel-stop-{os.getpid()}"
SALIDA_ORDENADA = 3.0


def _pedir_salida(nombre=NOMBRE_EVENTO, espera=SALIDA_ORDENADA, proc=None) -> bool:
    """Signals the sidecar to close by itself. -> whether it did.

    This matters for more than tidiness. LibreHardwareMonitor loads a kernel driver
    on Computer.Open() -- with a 0.9.3-class DLL that driver is WinRing0, on the
    Windows vulnerable-driver blocklist -- and it is Close() that removes its
    service. TerminateProcess runs no cleanup at all, so killing the sidecar left
    the driver loaded on the machine after the panel was stopped, indefinitely.

    Best effort: on any failure the caller kills as before.
    """
    if proc is None or proc.poll() is not None:
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenEventW(0x0002, False, nombre)      # EVENT_MODIFY_STATE
        if not h:
            return False
        try:
            k32.SetEvent(h)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False
    try:
        proc.wait(timeout=espera)
        return True
    except Exception:
        return False


def _kill(proc):
    """Asks the sidecar to close, then terminates it and reaps it.

    Reaping the process is what releases the handle and the DLL it had loaded. A
    wait() that overruns its timeout must not bring the caller down -- we are on
    the shutdown path precisely -- so it is swallowed.
    """
    if proc is None:
        return
    if _pedir_salida(proc=proc):
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
            env={**os.environ, "VMAXPANEL_STOP_EVENT": NOMBRE_EVENTO},
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
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="vmaxpanel-sidecar")
        self._thread.start()
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
                    # close() may have run between the while check and this spawn:
                    # it killed the previous process (or none, if this was the
                    # first) and this newborn is left with no owner. Without this
                    # guard a powershell stays alive holding
                    # LibreHardwareMonitorLib.dll.
                    return
                for line in proc.stdout:
                    if self._stop.is_set():
                        break
                    line = line.strip()
                    if not line.startswith("{"):
                        continue        # the sidecar can write stray warnings
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
                # Every pass closes ITS process. Without this, a respawn left the
                # previous one neither terminated nor reaped -- a zombie with its
                # stdout open -- and the `return` in the guard above left alive
                # exactly the one it had just created.
                _kill(proc)
            if not self._restart or self._stop.is_set():
                return
            # _stop.wait and not time.sleep: the backoff reaches 10 s and
            # time.sleep does not notice close(), so the reader thread stayed alive
            # that whole time after somebody asked it to stop.
            if self._stop.wait(BACKOFF[min(attempt, len(BACKOFF) - 1)]):
                return
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
        """Brings the sidecar down and waits for it to really die.

        The wait() is not a luxury: terminate() only asks, and a caller that then
        deletes or moves the directory can still hit the lock on
        LibreHardwareMonitorLib.dll -- the trap this module claims to avoid. The
        process is taken under the lock but killed outside it, because wait()
        blocks and namespace()/caps() need that lock.
        """
        self._stop.set()
        with self._lock:
            proc = self._proc
        _kill(proc)
        # join: close() returns when the reader thread has really finished, not
        # when it was asked to. Without this the caller has no way of knowing when
        # nobody is touching the process any more.
        #
        # Unless the caller IS the reader thread: join() on the current thread
        # raises RuntimeError, and close() must never raise -- it is used on
        # shutdown paths and inside a finally. In that case _stop is enough: the
        # thread itself will find the flag on its way back from here.
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=KILL_TIMEOUT)
