"""Metricas que no necesitan privilegios ni hardware especifico.

`cpu.load` es `% Processor Time` — la carga real. NO es `% Processor Utility`,
que es lo que usaba LCD Control (carga x clock/base) y saturaba en 100 con
carga real >= ~61%.
"""
import platform
import time

import psutil

from ..metrics import MetricSpec, short_cpu_name, slug, spec_for
from .base import Provider

DIAS = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO",
         "SEP", "OCT", "NOV", "DIC"]

_SERVED = {
    "cpu.name", "cpu.name_short", "cpu.load", "cpu.clock",
    "mem.load", "mem.used", "mem.total",
    "net.down", "net.up",
    "clock.time", "clock.time_hms", "clock.date",
}


class PsutilProvider(Provider):
    id = "psutil"

    def __init__(self, date_fmt=None):
        self._date_fmt = date_fmt
        self._cpu_name = platform.processor() or "CPU"
        c = psutil.net_io_counters()
        self._prev = (c.bytes_recv, c.bytes_sent, time.time())
        self._nics = self._adaptadores()
        self._prev_nic = {}
        psutil.cpu_percent(interval=None)      # arma la linea base

    def probe(self) -> bool:
        return True

    def metrics(self) -> set[str]:
        ids = set(_SERVED)
        for sl in self._nics:
            ids.add(f"net.{sl}.down")
            ids.add(f"net.{sl}.up")
        return ids

    def read(self):
        t = time.localtime()
        vm = psutil.virtual_memory()
        down, up = self._net_rate()
        freq = psutil.cpu_freq()
        return {
            "cpu.name": self._cpu_name.upper(),
            # platform.processor() en Windows da "Intel64 Family 6 Model
            # 151..." -- sin modelo que acortar. short_cpu_name() devuelve el
            # original cuando no encuentra nada que sacar, asi que el widget
            # muestra algo en vez de un hueco. El nombre lindo lo sirve el
            # sidecar por PDH, que tiene mas prioridad que este provider.
            "cpu.name_short": short_cpu_name(self._cpu_name).upper(),
            "cpu.load": psutil.cpu_percent(interval=None),
            "cpu.clock": float(freq.current) if freq else None,
            "mem.load": vm.percent,
            "mem.used": vm.used / (1024 ** 3),
            "mem.total": vm.total / (1024 ** 3),
            "net.down": down,
            "net.up": up,
            "clock.time": time.strftime("%H:%M", t),
            # Con segundos, para un panel a 30 fps: el segundero moviendose es la
            # senal mas barata de que lo que se ve esta vivo y no congelado.
            "clock.time_hms": time.strftime("%H:%M:%S", t),
            "clock.date": self._date(t),
            **self._net_rate_por_adaptador(),
        }

    def _date(self, t):
        if self._date_fmt:
            return time.strftime(self._date_fmt, t)
        return f"{DIAS[t.tm_wday]} {t.tm_mday} {MESES[t.tm_mon - 1]}"

    def _net_rate(self):
        c = psutil.net_io_counters()
        now = time.time()
        # max(0.2, ...): la primera lectura puede caer a microsegundos de la
        # linea base tomada en __init__, y dividir por un dt de ~0 convierte
        # cualquier diferencia de bytes en una tasa absurda de gigabytes por
        # segundo. Es una guarda contra la division, no una cadencia.
        dt = max(0.2, now - self._prev[2])
        down = (c.bytes_recv - self._prev[0]) / dt
        up = (c.bytes_sent - self._prev[1]) / dt
        self._prev = (c.bytes_recv, c.bytes_sent, now)
        return down, up

    # --- red por adaptador ---
    #
    # net.down/net.up son el total de la maquina, que con dos placas o con una
    # VPN levantada no dice de cual es el trafico. El id lleva un slug del
    # nombre del adaptador y el nombre real va en el catalogo.

    def _adaptadores(self) -> dict:
        """{slug: nombre real} de los adaptadores con trafico contabilizado.

        Se descubren una sola vez, en __init__: Registry lee metrics() en su
        constructor, asi que un adaptador que aparezca despues no se serviria
        igual, y recalcular la lista dejaria el conjunto de ids cambiando entre
        muestras -- lo mismo que ya causo el bug del indice de discos.
        """
        try:
            contadores = psutil.net_io_counters(pernic=True)
        except Exception:
            return {}
        # Se salta loopback: su trafico no le dice nada a nadie.
        return {slug(nombre): nombre for nombre in contadores
                if slug(nombre) and "loopback" not in nombre.lower()}

    def _net_rate_por_adaptador(self) -> dict:
        try:
            contadores = psutil.net_io_counters(pernic=True)
        except Exception:
            return {}
        ahora = time.time()
        out = {}
        for sl, nombre in self._nics.items():
            c = contadores.get(nombre)
            if c is None:
                out[f"net.{sl}.down"] = None
                out[f"net.{sl}.up"] = None
                continue
            previo = self._prev_nic.get(sl)
            if previo is None:
                out[f"net.{sl}.down"] = 0.0
                out[f"net.{sl}.up"] = 0.0
            else:
                dt = max(0.2, ahora - previo[2])
                out[f"net.{sl}.down"] = max(0.0, (c.bytes_recv - previo[0]) / dt)
                out[f"net.{sl}.up"] = max(0.0, (c.bytes_sent - previo[1]) / dt)
            self._prev_nic[sl] = (c.bytes_recv, c.bytes_sent, ahora)
        return out

    def catalog(self) -> dict:
        cat = {}
        for sl, nombre in self._nics.items():
            for medida, texto in (("down", "bajada"), ("up", "subida")):
                mid = f"net.{sl}.{medida}"
                base = spec_for(mid)
                if base is not None:
                    cat[mid] = MetricSpec(mid, f"{nombre} — {texto}", base.unit,
                                          base.kind, base.min, base.max)
        return cat

    def groups(self) -> dict:
        return {f"net.{sl}.{medida}": f"Red — {nombre}"
                for sl, nombre in self._nics.items()
                for medida in ("down", "up")}
