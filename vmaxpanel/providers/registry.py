"""Resuelve cada id de metrica al provider disponible de mayor prioridad."""
from ..metrics import UNAVAILABLE, is_metric
from .base import Provider

# Mas especifico primero: si una placa Gigabyte sirve cpu.temp por GSA1, eso
# le gana a la lectura generica de LibreHardwareMonitor.
PROVIDER_PRIORITY = ["gsa1", "msr", "pdh", "lhm", "psutil"]

_NO_PROVIDER = "ningun provider de esta maquina sirve esta metrica"


class Registry:
    def __init__(self, providers: list[Provider]):
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

        for p in self._available:
            for mid in p.metrics():
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
        out = {}
        for p in self._available:
            try:
                sample = p.read()
            except Exception as e:
                for mid in p.metrics():
                    if self._resolution.get(mid) == p.id:
                        self._degraded[mid] = f"provider {p.id} fallo: {e}"
                continue
            for mid in p.metrics():
                if self._resolution.get(mid) != p.id:
                    continue
                self._degraded.pop(mid, None)
                out[mid] = sample.get(mid)
        for mid in self.unavailable():
            out.setdefault(mid, UNAVAILABLE)
        return out

    def close(self):
        for p in self._providers:
            try:
                p.close()
            except Exception:
                pass
