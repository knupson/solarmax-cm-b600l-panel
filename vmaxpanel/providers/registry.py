"""Resuelve cada id de metrica al provider disponible de mayor prioridad."""
from ..metrics import UNAVAILABLE, is_metric
from .base import Provider

# Mas especifico primero: si una placa Gigabyte sirve cpu.temp por GSA1, eso
# le gana a la lectura generica de LibreHardwareMonitor.
PROVIDER_PRIORITY = ["gsa1", "msr", "pdh", "lhm", "smbios", "psutil"]

_NO_PROVIDER = "ningun provider de esta maquina sirve esta metrica"


class Registry:
    def __init__(self, providers: list[Provider]):
        # metrics() se lee una sola vez, aca. Un provider cuyo metrics() es
        # dinamico (ej. LhmProvider.served, que descubre disk.temp.N de la
        # primera muestra del sidecar) queda fijado a lo que tenia en este
        # instante para toda la vida de este Registry.
        for p in providers:
            for mid in p.metrics():
                if not is_metric(mid):
                    raise ValueError(
                        f"provider {p.id!r} declara una metrica desconocida: {mid!r}")

        self._providers = sorted(providers, key=self._rank)
        self._available = []
        self._reasons: dict[str, str] = {}
        self._resolution: dict[str, str] = {}

        for p in self._providers:
            try:
                ok = p.probe()
            except Exception as e:                      # un probe roto no tumba el arranque
                ok, p.unavailable_reason = False, f"fallo al detectar: {e}"
            if ok:
                self._available.append(p)
            else:
                reason = p.unavailable_reason or _NO_PROVIDER
                for mid in p.metrics():
                    self._reasons.setdefault(mid, reason)

        # Por metrica, TODOS los providers disponibles que la sirven, en orden
        # de prioridad. self._available ya viene ordenado, asi que cada lista
        # sale ordenada. Es lo que permite el failover de read(): antes solo se
        # guardaba el ganador y los suplentes quedaban invisibles.
        self._servers: dict[str, list[str]] = {}
        for p in self._available:
            for mid in p.metrics():
                self._servers.setdefault(mid, []).append(p.id)
                self._resolution.setdefault(mid, p.id)
                self._reasons.pop(mid, None)

        self._degraded: dict[str, str] = {}

    @staticmethod
    def _rank(p):
        try:
            return PROVIDER_PRIORITY.index(p.id)
        except ValueError:
            return len(PROVIDER_PRIORITY)

    def resolution(self) -> dict[str, str]:
        """metric id -> provider id que la sirve ahora."""
        return {m: pid for m, pid in self._resolution.items()
                if m not in self._degraded}

    def unavailable(self) -> dict[str, str]:
        """metric id -> motivo, en lenguaje llano, para mostrar en el editor."""
        return {**self._reasons, **self._degraded}

    def read(self):
        """Una muestra por vuelta, resolviendo cada metrica al provider de
        mayor prioridad que EFECTIVAMENTE respondio esta vez.

        La version anterior fijaba el dueno al arrancar y, cuando fallaba, se
        limitaba a marcar la metrica degradada: los suplentes quedaban
        salteados por `self._resolution.get(mid) != p.id`. Con cpu.clock y
        cpu.name servidas por pdh y por psutil, una caida de pdh mandaba las
        dos a "--" con psutil vivo al lado sirviendolas igual.

        La resolucion se recalcula en cada vuelta, asi que el failover y la
        vuelta atras (cuando el dueno original revive) salen del mismo
        camino, sin estado extra que sincronizar.
        """
        samples, errors = {}, {}
        for p in self._available:
            try:
                samples[p.id] = p.read()
            except Exception as e:
                errors[p.id] = f"provider {p.id} fallo: {e}"

        out, degraded = {}, {}
        for mid, pids in self._servers.items():
            for pid in pids:
                if pid in samples:
                    self._resolution[mid] = pid
                    # .get(): el provider respondio pero puede no traer esta
                    # metrica en esta muestra. None es "sin dato ahora", que
                    # no es lo mismo que UNAVAILABLE.
                    out[mid] = samples[pid].get(mid)
                    break
            else:
                degraded[mid] = next((errors[pid] for pid in pids if pid in errors),
                                     _NO_PROVIDER)
        self._degraded = degraded

        for mid in self.unavailable():
            out.setdefault(mid, UNAVAILABLE)
        return out

    def close(self):
        for p in self._providers:
            try:
                p.close()
            except Exception:
                pass
