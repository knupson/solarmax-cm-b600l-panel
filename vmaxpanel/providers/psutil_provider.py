"""Metrics that need neither privileges nor specific hardware.

`cpu.load` is `% Processor Time` — the real load. It is NOT `% Processor
Utility`, which is what LCD Control used (load x clock/base) and which saturated
at 100 for any real load above roughly 61%.
"""
import platform
import time

import locale

import psutil

from ..metrics import MetricSpec, short_cpu_name, slug, spec_for
from .base import Provider

# The date follows the machine's regional settings. It used to come from
# hardcoded Spanish tables, so every profile -- including the English ones --
# showed a Spanish date as the largest text on the panel.
#
# The setlocale call is what makes strftime read Windows' settings at all: without
# it Python stays in the "C" locale and %a/%b are always English. Only LC_TIME is
# touched, so number formatting elsewhere is unaffected. It is best effort: on a
# locale the C runtime will not accept, %a/%b fall back to English, which is a
# worse date but not a broken panel.
FORMATO_FECHA = "%a %d %b"

try:
    locale.setlocale(locale.LC_TIME, "")
except locale.Error:
    pass

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
        psutil.cpu_percent(interval=None)      # establishes the baseline

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
            # platform.processor() on Windows gives "Intel64 Family 6 Model
            # 151..." -- with no model to shorten. short_cpu_name() returns the
            # original when it finds nothing to strip, so the widget shows
            # something instead of a hole. The pretty name is served by the sidecar
            # through PDH, which outranks this provider.
            "cpu.name_short": short_cpu_name(self._cpu_name).upper(),
            "cpu.load": psutil.cpu_percent(interval=None),
            "cpu.clock": float(freq.current) if freq else None,
            "mem.load": vm.percent,
            "mem.used": vm.used / (1024 ** 3),
            "mem.total": vm.total / (1024 ** 3),
            "net.down": down,
            "net.up": up,
            "clock.time": time.strftime("%H:%M", t),
            # With seconds, for a panel at 30 fps: a moving seconds field is the
            # cheapest signal that what you see is alive and not frozen.
            "clock.time_hms": time.strftime("%H:%M:%S", t),
            "clock.date": self._date(t),
            **self._net_rate_por_adaptador(),
        }

    def _date(self, t):
        # Uppercased only on the default: the panel's fonts are used uppercase
        # throughout. An explicit format is honoured exactly as written.
        if self._date_fmt:
            return time.strftime(self._date_fmt, t)
        return time.strftime(FORMATO_FECHA, t).upper()

    def _net_rate(self):
        c = psutil.net_io_counters()
        now = time.time()
        # max(0.2, ...): the first reading can land microseconds after the baseline
        # taken in __init__, and dividing by a dt of ~0 turns any difference in
        # bytes into an absurd rate of gigabytes per second. It is a guard on the
        # division, not a cadence.
        dt = max(0.2, now - self._prev[2])
        down = (c.bytes_recv - self._prev[0]) / dt
        up = (c.bytes_sent - self._prev[1]) / dt
        self._prev = (c.bytes_recv, c.bytes_sent, now)
        return down, up

    # --- red por adaptador ---
    #
    # net.down/net.up are the machine total, which with two NICs or a VPN up does
    # not say whose traffic it is. The id carries a slug of the adapter name and
    # the real name goes in the catalogue.

    def _adaptadores(self) -> dict:
        """{slug: real name} for the adapters whose traffic is counted.

        They are discovered once, in __init__: Registry reads metrics() in its
        constructor, so an adapter appearing later would not be served anyway, and
        recalculating the list would leave the set of ids changing between samples
        -- the same thing that already caused the disk index bug.
        """
        try:
            contadores = psutil.net_io_counters(pernic=True)
        except Exception:
            return {}
        # Loopback is skipped: its traffic tells nobody anything.
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
