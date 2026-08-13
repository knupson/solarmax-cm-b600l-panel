"""Resuelve cada id de metrica al provider disponible de mayor prioridad."""
from ..metrics import UNAVAILABLE, group_for, is_metric, spec_for
from .base import Provider

# Mas especifico primero: si una placa Gigabyte sirve cpu.temp por GSA1, eso
# le gana a la lectura generica de LibreHardwareMonitor.
# gsa1 antes que cpulhm: la temp de CPU por GSA1 es la del sensor de la
# placa, mas cercana a lo que reporta el BIOS que el promedio de nucleos.
PROVIDER_PRIORITY = ["gsa1", "cpulhm", "mobo", "msr", "pdh", "lhm",
                     "smbios", "wmi", "psutil"]

_NO_PROVIDER = "no provider on this machine serves this metric"


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
                        f"provider {p.id!r} declares an unknown metric: {mid!r}")

        self._providers = sorted(providers, key=self._rank)
        self._available = []
        self._reasons: dict[str, str] = {}
        self._resolution: dict[str, str] = {}

        for p in self._providers:
            try:
                ok = p.probe()
            except Exception as e:                      # un probe roto no tumba el arranque
                ok, p.unavailable_reason = False, f"detection failed: {e}"
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

    def catalog(self) -> dict:
        """id -> MetricSpec con la mejor etiqueta disponible, para el editor.

        Solo de los providers DISPONIBLES: ofrecerle al usuario una metrica que
        nadie sirve es invitarlo a poner un widget que va a mostrar "--".

        La etiqueta del provider gana sobre la generica de metrics.spec_for()
        porque es la unica que puede nombrar el dispositivo real: `vol.D.free`
        no sabe que la D se llama "JUEGOS".
        """
        cat = {}
        for mid in self._servers:
            base = spec_for(mid)
            if base is not None:
                cat[mid] = base
        for p in self._available:
            try:
                cat.update(p.catalog())
            except Exception:
                pass                    # un catalogo roto no puede tumbar al editor
        return cat

    def groups(self) -> dict:
        """id -> dispositivo, para agrupar la lista del editor.

        Lo que el provider no clasifique cae al grupo por prefijo de
        metrics.group_for(), que ya devuelve un nombre amigable ("net" -> "Red")
        en vez del prefijo crudo.
        """
        g = {}
        for mid in self._servers:
            g[mid] = group_for(mid)
        for p in self._available:
            try:
                g.update(p.groups())
            except Exception:
                pass
        return g

    def unavailable(self) -> dict[str, str]:
        """metric id -> motivo, en lenguaje llano, para mostrar en el editor.

        Solo lo que tiene un motivo concreto: un provider que no arranco (con SU
        motivo, "WinRing0 esta en la blocklist", que dice que hacer) y lo que se
        degrado en la ultima muestra.

        **A proposito NO lista todas las metricas que nadie sirve.** Lo intente y era
        ruido: en una maquina sin GPU son 27 lineas de "sin datos" aunque el perfil no
        use ni una metrica de GPU, y una lista de problemas que siempre tiene 27
        entradas es una lista que el usuario deja de leer. Lo que importa es lo que el
        layout ACTIVO usa y no se puede servir, y eso lo reporta Engine._sin_datos(),
        que es el unico que tiene el layout adelante -- ademas de ser el unico camino
        posible para las metricas de familia (fan.N.rpm), que son un patron y no se
        pueden enumerar.
        """
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
