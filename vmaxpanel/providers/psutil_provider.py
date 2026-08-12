"""Metricas que no necesitan privilegios ni hardware especifico.

`cpu.load` es `% Processor Time` — la carga real. NO es `% Processor Utility`,
que es lo que usaba LCD Control (carga x clock/base) y saturaba en 100 con
carga real >= ~61%.
"""
import platform
import time

import psutil

from ..metrics import short_cpu_name
from .base import Provider

DIAS = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO",
         "SEP", "OCT", "NOV", "DIC"]

_SERVED = {
    "cpu.name", "cpu.name_short", "cpu.load", "cpu.clock",
    "mem.load", "mem.used", "mem.total",
    "net.down", "net.up",
    "clock.time", "clock.date",
}


class PsutilProvider(Provider):
    id = "psutil"

    def __init__(self, date_fmt=None):
        self._date_fmt = date_fmt
        self._cpu_name = platform.processor() or "CPU"
        c = psutil.net_io_counters()
        self._prev = (c.bytes_recv, c.bytes_sent, time.time())
        psutil.cpu_percent(interval=None)      # arma la linea base

    def probe(self) -> bool:
        return True

    def metrics(self) -> set[str]:
        return set(_SERVED)

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
            "clock.date": self._date(t),
        }

    def _date(self, t):
        if self._date_fmt:
            return time.strftime(self._date_fmt, t)
        return f"{DIAS[t.tm_wday]} {t.tm_mday} {MESES[t.tm_mon - 1]}"

    def _net_rate(self):
        c = psutil.net_io_counters()
        now = time.time()
        dt = max(0.2, now - self._prev[2])
        down = (c.bytes_recv - self._prev[0]) / dt
        up = (c.bytes_sent - self._prev[1]) / dt
        self._prev = (c.bytes_recv, c.bytes_sent, now)
        return down, up
