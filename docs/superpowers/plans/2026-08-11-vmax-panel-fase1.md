# VMax Panel — Fase 1: motor data-driven — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el layout hardcodeado de `daemon/panel.py` por un motor manejado por datos: registry de métricas, providers de sensores con degradado, `layout.json` validado, renderer de widgets y recarga en caliente — con paridad visual respecto de lo que el panel muestra hoy.

**Architecture:** Paquete `vmaxpanel/` nuevo, al lado del `daemon/` existente que sigue funcionando sin cambios hasta el final de la fase. Los sensores se abstraen en providers que declaran disponibilidad (`probe()`) y qué ids canónicos sirven; un registry los resuelve por prioridad y marca `UNAVAILABLE` lo que nadie sirve. El layout es un JSON validado por un validador propio y renderizado por un único módulo PIL compartido, que en fases siguientes también usa el editor.

**Tech Stack:** Python 3.13, Pillow 12.2, psutil 7.2, pyserial, pytest. Sidecar PowerShell + LibreHardwareMonitorLib.dll (MPL-2.0) para GPU/SSD, GSA1 ACPI-WMI para temps de CPU/VRM/VCore.

## Global Constraints

- **Solo lecturas de hardware.** GSA1 expone `PIOWrite`, `MEMWrite`, `PCIWrite` (escritura arbitraria a puertos I/O, memoria física y espacio PCI). Ningún método de escritura se invoca, en ningún provider.
- **No se empaquetan TTFs.** `consola.ttf`/`consolab.ttf` son Consolas, de Microsoft, no redistribuibles. Las fuentes se resuelven por nombre de familia contra `assets/fonts/` y después contra las del sistema.
- **No se usa `back.png`.** Es arte del tema Vitals de LCD Control. El fondo por defecto es `gradient`, generado en código, original.
- **Nada específico de esta máquina queda quemado**: puerto (autodetección VID_33C3/PID_F101), geometría (parseada del SN), rotación (setting), nombres de CPU/GPU (providers), base clock (`Win32_Processor.MaxClockSpeed`).
- **Sin `eval` ni expresiones evaluables** en layouts. Las reglas de color son comparadores. Los layouts se comparten entre usuarios.
- **Sin dependencias nuevas más allá de `pytest`.** El schema se valida con un validador propio.
- **`SUPPORTED_VERSION = 1`.** Un layout con `version` mayor se rechaza con mensaje claro.
- Ids canónicos de métrica, exactos: `cpu.name cpu.load cpu.temp cpu.clock cpu.vcore cpu.vrm_temp cpu.power cpu.fan gpu.name gpu.load gpu.temp gpu.hotspot gpu.clock gpu.power gpu.vram gpu.fan mem.load mem.used mem.total net.down net.up disk.temp.N clock.time clock.date`
- Prioridad de providers, exacta: `["gsa1", "msr", "pdh", "lhm", "psutil"]`.

---

## File Structure

```
vmaxpanel/
  __init__.py
  metrics.py                  registry de MetricSpec, sentinel UNAVAILABLE
  providers/
    __init__.py
    base.py                   Provider ABC
    psutil_provider.py        cpu.load/clock fallback, mem.*, net.*
    sidecar.py                proceso PowerShell + pump de JSON + restart con backoff
    sidecar_providers.py      Gsa1Provider, PdhProvider, LhmProvider sobre un SidecarClient
    msr.py                    stub que reporta por qué no está disponible
    registry.py               resolución por prioridad, UNAVAILABLE
  layout/
    __init__.py
    model.py                  dataclasses Layout/PanelCfg/Background/Widget*
    schema.py                 validate(raw) -> list[str]
    loader.py                 load/loads/save + ProfileStore (keep-previous)
  render/
    __init__.py
    fonts.py                  índice de familias + resolución con fallback
    widgets.py                draw_* por tipo de widget
    background.py             solid / gradient / image
    renderer.py               Renderer.frame(sample, history)
  transport/
    __init__.py
    panel_link.py             autodetección, SN, handshake, brillo, envío, FakeTransport
  engine.py                   loop, cadencias, hot-reload
  cli.py                      python -m vmaxpanel
  sensors.ps1                 sidecar nuevo: ids canónicos, caps, base clock detectado
  lib/                        copia de las DLL de terceros que necesita el sidecar
  profiles/
    vitals.json               perfil por defecto, paridad con el panel actual
daemon/                       SIN TOCAR: es la vuelta atrás durante toda la fase 1
tests/
  test_metrics.py  test_providers.py  test_registry.py  test_schema.py
  test_loader.py  test_fonts.py  test_widgets.py  test_background.py
  test_renderer.py  test_panel_link.py  test_engine.py  test_vitals_profile.py
  golden/vitals.png
```

---

### Task 1: Repositorio, esqueleto del paquete y registry de métricas

El proyecto va a distribuirse, así que primero pasa a ser un repo git. Después el paquete y el registry de métricas, que es de lo que dependen todos los módulos siguientes.

**Files:**
- Create: `.gitignore`, `vmaxpanel/__init__.py`, `vmaxpanel/metrics.py`
- Create: `tests/__init__.py`, `tests/test_metrics.py`
- Create: `pytest.ini`

**Interfaces:**
- Consumes: nada.
- Produces: `vmaxpanel.metrics.UNAVAILABLE` (singleton falsy), `MetricSpec(id, label, unit, kind, min, max)`, `METRICS: dict[str, MetricSpec]`, `is_metric(mid: str) -> bool`, `disk_metric(n: int) -> str`.

- [x] **Step 1: Inicializar el repo y `.gitignore`** — YA HECHO por el controlador

El repo se inicializó antes de empezar la ejecución, con `.gitignore` en la raíz y un commit inicial (`36e359f`) sobre la rama `fase1-motor-data-driven`. Las DLL, los TTF de Consolas y `back.png` quedan excluidos por licencia; `transcript/` y `research/` por no ser código. **No repitas este paso**: verificá que `.gitignore` exista y arrancá en el Step 2.

- [ ] **Step 2: Instalar pytest y configurarlo**

```bash
python -m pip install pytest
```

`pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q
```

- [ ] **Step 3: Escribir el test que falla**

`tests/test_metrics.py`:

```python
from vmaxpanel.metrics import METRICS, UNAVAILABLE, MetricSpec, disk_metric, is_metric


def test_unavailable_is_falsy_and_singleton():
    assert not UNAVAILABLE
    assert UNAVAILABLE is type(UNAVAILABLE)()
    assert repr(UNAVAILABLE) == "UNAVAILABLE"


def test_core_metrics_are_registered():
    for mid in ("cpu.load", "cpu.temp", "gpu.load", "mem.used", "net.down",
                "clock.time", "cpu.name"):
        assert mid in METRICS, mid


def test_metric_spec_shape():
    spec = METRICS["cpu.load"]
    assert isinstance(spec, MetricSpec)
    assert spec.unit == "%"
    assert spec.kind == "number"
    assert (spec.min, spec.max) == (0.0, 100.0)


def test_name_metrics_are_text_kind():
    assert METRICS["cpu.name"].kind == "text"
    assert METRICS["clock.time"].kind == "text"


def test_disk_metrics_are_positional():
    assert disk_metric(0) == "disk.temp.0"
    assert is_metric("disk.temp.0")
    assert is_metric("disk.temp.7")
    assert not is_metric("disk.temp.x")
    assert not is_metric("cpu.powr")
```

- [ ] **Step 4: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel'`

- [ ] **Step 5: Implementar**

`vmaxpanel/__init__.py`:

```python
"""VMax Panel — driver y editor para el panel HL-VMAX (VID_33C3/PID_F101)."""

__version__ = "0.1.0"
```

`vmaxpanel/metrics.py`:

```python
"""Ids canonicos de metrica, desacoplados de su origen.

Un id que ningun provider disponible sirve se lee como UNAVAILABLE, estado
distinto de None: None es "el provider existe pero esta muestra no trajo valor".
"""
import re
from dataclasses import dataclass


class _Unavailable:
    """Sentinel: ningun provider de esta maquina sirve esta metrica."""

    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __bool__(self):
        return False

    def __repr__(self):
        return "UNAVAILABLE"


UNAVAILABLE = _Unavailable()


@dataclass(frozen=True)
class MetricSpec:
    id: str
    label: str
    unit: str
    kind: str            # "number" | "text"
    min: float | None = None
    max: float | None = None


def _n(mid, label, unit, lo=None, hi=None):
    return MetricSpec(mid, label, unit, "number", lo, hi)


def _t(mid, label):
    return MetricSpec(mid, label, "", "text")


METRICS: dict[str, MetricSpec] = {m.id: m for m in [
    _t("cpu.name", "Modelo de CPU"),
    _n("cpu.load", "Carga de CPU", "%", 0.0, 100.0),
    _n("cpu.temp", "Temperatura de CPU", "°C", 0.0, 110.0),
    _n("cpu.clock", "Clock de CPU", "MHz", 0.0, 6000.0),
    _n("cpu.vcore", "VCore", "V", 0.0, 2.0),
    _n("cpu.vrm_temp", "Temperatura de VRM", "°C", 0.0, 120.0),
    _n("cpu.power", "Consumo de CPU", "W", 0.0, 300.0),
    _n("cpu.fan", "Fan de CPU", "RPM", 0.0, 3000.0),
    _t("gpu.name", "Modelo de GPU"),
    _n("gpu.load", "Carga de GPU", "%", 0.0, 100.0),
    _n("gpu.temp", "Temperatura de GPU", "°C", 0.0, 110.0),
    _n("gpu.hotspot", "Hot spot de GPU", "°C", 0.0, 130.0),
    _n("gpu.clock", "Clock de GPU", "MHz", 0.0, 4000.0),
    _n("gpu.power", "Consumo de GPU", "W", 0.0, 600.0),
    _n("gpu.vram", "VRAM usada", "%", 0.0, 100.0),
    _n("gpu.fan", "Fan de GPU", "RPM", 0.0, 4000.0),
    _n("mem.load", "RAM usada", "%", 0.0, 100.0),
    _n("mem.used", "RAM usada", "GiB", 0.0, 256.0),
    _n("mem.total", "RAM total", "GiB", 0.0, 256.0),
    _n("net.down", "Bajada", "B/s", 0.0, None),
    _n("net.up", "Subida", "B/s", 0.0, None),
    _t("clock.time", "Hora"),
    _t("clock.date", "Fecha"),
]}

_DISK_RE = re.compile(r"^disk\.temp\.(\d+)$")

DISK_TEMP_SPEC = _n("disk.temp.N", "Temperatura de disco", "°C", 0.0, 100.0)


def disk_metric(n: int) -> str:
    return f"disk.temp.{n}"


def is_metric(mid: str) -> bool:
    return mid in METRICS or bool(_DISK_RE.match(mid))


def spec_for(mid: str) -> MetricSpec | None:
    if mid in METRICS:
        return METRICS[mid]
    if _DISK_RE.match(mid):
        return MetricSpec(mid, DISK_TEMP_SPEC.label, DISK_TEMP_SPEC.unit,
                          "number", DISK_TEMP_SPEC.min, DISK_TEMP_SPEC.max)
    return None
```

- [ ] **Step 6: Correr el test y verificar que pasa**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: PASS, 5 tests

- [ ] **Step 7: Commit**

```bash
git add .gitignore pytest.ini vmaxpanel/__init__.py vmaxpanel/metrics.py tests/__init__.py tests/test_metrics.py
git commit -m "feat: registry de metricas canonicas y sentinel UNAVAILABLE"
```

---

### Task 2: Provider base, provider psutil y registry con degradado

**Files:**
- Create: `vmaxpanel/providers/__init__.py`, `vmaxpanel/providers/base.py`, `vmaxpanel/providers/psutil_provider.py`, `vmaxpanel/providers/registry.py`
- Test: `tests/test_providers.py`, `tests/test_registry.py`

**Interfaces:**
- Consumes: `vmaxpanel.metrics.{UNAVAILABLE, is_metric}`
- Produces:
  - `Provider` ABC: atributos `id: str`, `unavailable_reason: str | None`; métodos `probe() -> bool`, `metrics() -> set[str]`, `read() -> dict[str, float | str | None]`, `close() -> None`.
  - `PsutilProvider()` — sirve `cpu.load cpu.clock cpu.name mem.load mem.used mem.total net.down net.up clock.time clock.date`.
  - `Registry(providers: list[Provider])` con `resolution() -> dict[str, str]`, `unavailable() -> dict[str, str]`, `read() -> dict[str, float | str | _Unavailable]`, `close()`.
  - `PROVIDER_PRIORITY: list[str]`.

- [ ] **Step 1: Escribir los tests que fallan**

`tests/test_providers.py`:

```python
import time

from vmaxpanel.metrics import METRICS
from vmaxpanel.providers.psutil_provider import PsutilProvider


def test_psutil_provider_probes_true():
    p = PsutilProvider()
    assert p.id == "psutil"
    assert p.probe() is True
    assert p.unavailable_reason is None


def test_psutil_declares_only_registered_metrics():
    p = PsutilProvider()
    assert p.metrics() <= set(METRICS)
    assert {"cpu.load", "mem.used", "net.down", "clock.time"} <= p.metrics()


def test_psutil_read_returns_declared_keys_only():
    p = PsutilProvider()
    p.read()                      # primer llamada arma la linea base de red
    time.sleep(0.2)
    sample = p.read()
    assert set(sample) == p.metrics()
    assert 0.0 <= sample["cpu.load"] <= 100.0
    assert sample["mem.used"] > 0.0
    assert sample["net.down"] >= 0.0
    assert isinstance(sample["cpu.name"], str) and sample["cpu.name"]


def test_psutil_clock_and_date_are_strings():
    sample = PsutilProvider().read()
    assert len(sample["clock.time"]) == 5 and sample["clock.time"][2] == ":"
    assert isinstance(sample["clock.date"], str)
```

`tests/test_registry.py`:

```python
import pytest

from vmaxpanel.metrics import UNAVAILABLE
from vmaxpanel.providers.base import Provider
from vmaxpanel.providers.registry import Registry


class Fake(Provider):
    def __init__(self, pid, served, values=None, ok=True, reason=None):
        self.id = pid
        self._served = set(served)
        self._values = values or {m: 1.0 for m in served}
        self._ok = ok
        self.unavailable_reason = reason
        self.closed = False

    def probe(self):
        return self._ok

    def metrics(self):
        return self._served

    def read(self):
        return dict(self._values)

    def close(self):
        self.closed = True


def test_unprobed_provider_serves_nothing():
    r = Registry([Fake("gsa1", ["cpu.temp"], ok=False, reason="requiere placa Gigabyte")])
    assert r.resolution() == {}
    assert r.unavailable()["cpu.temp"] == "requiere placa Gigabyte"
    assert r.read()["cpu.temp"] is UNAVAILABLE


def test_priority_prefers_more_specific_provider():
    r = Registry([
        Fake("lhm", ["cpu.temp"], {"cpu.temp": 50.0}),
        Fake("gsa1", ["cpu.temp"], {"cpu.temp": 42.0}),
    ])
    assert r.resolution()["cpu.temp"] == "gsa1"
    assert r.read()["cpu.temp"] == 42.0


def test_priority_is_independent_of_construction_order():
    a = Registry([Fake("gsa1", ["cpu.temp"]), Fake("lhm", ["cpu.temp"])])
    b = Registry([Fake("lhm", ["cpu.temp"]), Fake("gsa1", ["cpu.temp"])])
    assert a.resolution() == b.resolution()


def test_metric_nobody_serves_is_unavailable_with_reason():
    r = Registry([Fake("psutil", ["cpu.load"])])
    sample = r.read()
    assert sample["cpu.load"] == 1.0
    assert "cpu.power" not in r.resolution()


def test_none_value_is_not_unavailable():
    r = Registry([Fake("gsa1", ["cpu.temp"], {"cpu.temp": None})])
    assert r.read()["cpu.temp"] is None


def test_provider_exception_degrades_without_killing_read():
    class Boom(Fake):
        def read(self):
            raise RuntimeError("wmi murio")

    r = Registry([Boom("gsa1", ["cpu.temp"]), Fake("psutil", ["cpu.load"])])
    sample = r.read()
    assert sample["cpu.temp"] is UNAVAILABLE
    assert sample["cpu.load"] == 1.0
    assert "wmi murio" in r.unavailable()["cpu.temp"]


def test_unknown_metric_id_is_rejected_at_construction():
    with pytest.raises(ValueError, match="cpu.powr"):
        Registry([Fake("psutil", ["cpu.powr"])])


def test_close_closes_every_provider():
    f = Fake("psutil", ["cpu.load"])
    Registry([f]).close()
    assert f.closed
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_providers.py tests/test_registry.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel.providers'`

- [ ] **Step 3: Implementar el ABC**

`vmaxpanel/providers/__init__.py`:

```python
```

(archivo vacío a propósito: el paquete no re-exporta nada, para que importar un provider no arrastre `pyserial` ni el sidecar)

`vmaxpanel/providers/base.py`:

```python
"""Contrato de un provider de sensores.

Un provider declara que ids canonicos sirve y si existe en esta maquina.
SOLO lecturas: ningun provider invoca metodos de escritura de hardware.
"""
from abc import ABC, abstractmethod


class Provider(ABC):
    id: str = "?"
    unavailable_reason: str | None = None

    @abstractmethod
    def probe(self) -> bool:
        """True si este provider funciona en esta maquina.

        Si devuelve False debe dejar `unavailable_reason` con el motivo en
        lenguaje llano: es lo que el editor le muestra al usuario.
        """

    @abstractmethod
    def metrics(self) -> set[str]:
        """Ids canonicos que este provider sirve."""

    @abstractmethod
    def read(self) -> dict[str, float | str | None]:
        """Ultima muestra. Las claves deben ser subconjunto de metrics()."""

    def close(self) -> None:
        pass
```

- [ ] **Step 4: Implementar el provider psutil**

`vmaxpanel/providers/psutil_provider.py`:

```python
"""Metricas que no necesitan privilegios ni hardware especifico.

`cpu.load` es `% Processor Time` — la carga real. NO es `% Processor Utility`,
que es lo que usaba LCD Control (carga x clock/base) y saturaba en 100 con
carga real >= ~61%.
"""
import platform
import time

import psutil

from .base import Provider

DIAS = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO",
         "SEP", "OCT", "NOV", "DIC"]

_SERVED = {
    "cpu.name", "cpu.load", "cpu.clock",
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
```

`cpu.clock` de psutil queda como fallback: en Windows suele devolver el clock base, no el real. El provider `pdh` del sidecar lo sirve mejor y tiene prioridad más alta.

- [ ] **Step 5: Implementar el registry**

`vmaxpanel/providers/registry.py`:

```python
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
```

- [ ] **Step 6: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_providers.py tests/test_registry.py -v`
Expected: PASS, 12 tests

- [ ] **Step 7: Commit**

```bash
git add vmaxpanel/providers tests/test_providers.py tests/test_registry.py
git commit -m "feat: capa de providers con resolucion por prioridad y degradado"
```

---

### Task 3: Sidecar PowerShell con ids canónicos y capabilities

El sidecar actual emite claves propias (`cpu_temp`, `vrm_temp`, `gpu_load`, `disks[]`) y mezcla tres orígenes distintos en un solo objeto plano, con el base clock del i5-12400F quemado. Pasa a emitir **ids canónicos ya namespaceados por provider**, más un bloque `caps` que dice qué funcionó en esta máquina. Así el lado Python no traduce nombres y `probe()` se contesta con un dato real en vez de una adivinanza.

**Files:**
- Create: `vmaxpanel/sensors.ps1` (sidecar nuevo; `daemon/sensors.ps1` queda intacto como vuelta atrás)
- Create: `vmaxpanel/lib/` con copia de `LibreHardwareMonitorLib.dll`, `HidSharp.dll`, `HidLibrary.dll`
- Create: `vmaxpanel/providers/sidecar.py`, `vmaxpanel/providers/sidecar_providers.py`, `vmaxpanel/providers/msr.py`
- Test: `tests/test_sidecar.py`

**Por qué un archivo nuevo y no una reescritura:** `daemon/panel.py` lee las claves planas del
sidecar viejo (`cpu_temp`, `gpu_load`, `disks`). Reescribir `daemon/sensors.ps1` in place lo
dejaría mostrando `--` en todo el hardware al primer reinicio, y la Definición de Terminado
exige que el daemon viejo siga siendo una vuelta atrás intacta.

**Interfaces:**
- Consumes: `Provider` ABC de Task 2.
- Produces:
  - `SidecarClient(script: Path, spawn=None)` con `start()`, `caps() -> dict[str, bool]`, `namespace(name: str) -> dict`, `wait_ready(timeout: float) -> bool`, `fresh: bool`, `close()`. El parámetro `spawn` inyecta un lanzador falso en los tests.
  - `Gsa1Provider(client)`, `PdhProvider(client)`, `LhmProvider(client)` — ids `"gsa1"`, `"pdh"`, `"lhm"`.
  - `MsrProvider()` — id `"msr"`, `probe()` siempre False en fase 1 con motivo.

- [ ] **Step 1: Reescribir el sidecar**

`vmaxpanel/sensors.ps1` (las DLL se buscan en `$PSScriptRoot\lib\`):

```powershell
# Sidecar de sensores para VMax Panel.
# Emite una linea JSON por segundo a stdout, con ids canonicos de metrica ya
# namespaceados por provider, mas un bloque "caps" con lo que funciono aca.
#
#   gsa1  Gigabyte GSA1 ACPI-WMI (driverless): temp CPU (id2), temp VRM (id4), VCore (EZV id5)
#   pdh   % Processor Performance x base clock -> clock real de CPU
#   lhm   LibreHardwareMonitor: GPU y temps de SSD por SMART
#
# SOLO lecturas. GSA1 tambien expone PIOWrite/MEMWrite/PCIWrite (escritura
# arbitraria a puertos, memoria fisica y espacio PCI): no se invocan.
$ErrorActionPreference = 'SilentlyContinue'

$caps = [ordered]@{ gsa1 = $false; pdh = $false; lhm = $false }

# --- GSA1 (solo Gigabyte) ---
$gsa = Get-CimInstance -Namespace root\WMI -ClassName GSA1_ACPIMethod
if ($gsa) {
    $probe = (Invoke-CimMethod -InputObject $gsa -MethodName ZFCGetCurrentTemp -Arguments @{ id = [byte]2 }).value
    if ($null -ne $probe -and $probe -gt 0) { $caps.gsa1 = $true }
}

# --- base clock real de ESTA CPU (antes estaba quemado en 2500) ---
$baseMhz = (Get-CimInstance Win32_Processor | Select-Object -First 1).MaxClockSpeed
if (-not $baseMhz -or $baseMhz -le 0) { $baseMhz = 0 }
$cpuName = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name

# --- LHM ---
$comp = $null
try {
    Add-Type -Path "$PSScriptRoot\LibreHardwareMonitorLib.dll" -ErrorAction Stop
    $comp = New-Object LibreHardwareMonitor.Hardware.Computer
    $comp.IsGpuEnabled = $true
    $comp.IsStorageEnabled = $true
    $comp.Open()
    if ($comp.Hardware.Count -gt 0) { $caps.lhm = $true }
} catch { $comp = $null }

function Gsa-Temp([byte]$id) {
    (Invoke-CimMethod -InputObject $gsa -MethodName ZFCGetCurrentTemp -Arguments @{ id = $id }).value
}

function Sensor($hw, $type, $name) {
    ($hw.Sensors | Where-Object { $_.SensorType -eq $type -and $_.Name -eq $name } |
        Select-Object -First 1).Value
}

while ($true) {
    $out = [ordered]@{}

    if ($caps.gsa1) {
        $g = [ordered]@{}
        $g.'cpu.temp'     = Gsa-Temp 2
        $g.'cpu.vrm_temp' = Gsa-Temp 4
        $v = (Invoke-CimMethod -InputObject $gsa -MethodName EZVGetVoltage -Arguments @{ Id = 5 }).Value
        $g.'cpu.vcore'    = if ($v -gt 0) { [math]::Round($v / 1000.0, 3) } else { $null }
        $out.gsa1 = $g
    }

    if ($baseMhz -gt 0) {
        $perf = (Get-Counter '\Processor Information(_Total)\% Processor Performance' -MaxSamples 1).CounterSamples[0].CookedValue
        if ($null -ne $perf) {
            $caps.pdh = $true
            $out.pdh = [ordered]@{ 'cpu.clock' = [int]($baseMhz * $perf / 100.0); 'cpu.name' = $cpuName }
        }
    }

    if ($comp) {
        $l = [ordered]@{}
        $disk = 0
        foreach ($hw in $comp.Hardware) {
            $hw.Update()
            switch -Wildcard ("$($hw.HardwareType)") {
                'Gpu*' {
                    $l.'gpu.name'    = $hw.Name
                    $l.'gpu.load'    = Sensor $hw 'Load' 'GPU Core'
                    $l.'gpu.vram'    = Sensor $hw 'Load' 'GPU Memory'
                    $l.'gpu.temp'    = Sensor $hw 'Temperature' 'GPU Core'
                    $l.'gpu.hotspot' = Sensor $hw 'Temperature' 'GPU Hot Spot'
                    $l.'gpu.power'   = Sensor $hw 'Power' 'GPU Package'
                    $l.'gpu.clock'   = Sensor $hw 'Clock' 'GPU Core'
                    $l.'gpu.fan'     = Sensor $hw 'Fan' 'GPU Fan'
                }
                'Storage' {
                    $t = Sensor $hw 'Temperature' 'Temperature'
                    if ($null -ne $t) { $l."disk.temp.$disk" = $t; $disk++ }
                }
            }
        }
        $out.lhm = $l
    }

    $out.caps = $caps
    ($out | ConvertTo-Json -Compress -Depth 4)
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 900
}
```

`Gpu*` con `switch -Wildcard` reemplaza el `'GpuAmd'` literal: cubre `GpuNvidia` e `GpuIntel` sin tocar nada.

- [ ] **Step 2: Verificar a mano que el sidecar emite el formato nuevo**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File vmaxpanel\sensors.ps1`
Expected: una línea JSON por segundo con las claves `gsa1`, `pdh`, `lhm`, `caps`; en esta máquina `caps` debe dar `{"gsa1":true,"pdh":true,"lhm":true}`. Cortar con Ctrl+C.

Si `caps.gsa1` sale `false`, el daemon viejo está corriendo y hay contención por WMI: `daemon\stop.ps1` primero.

- [ ] **Step 3: Escribir los tests que fallan**

Los tests no lanzan PowerShell: inyectan un `spawn` falso que devuelve un objeto con `stdout` y `terminate()`. Así el sidecar se testea sin hardware.

`tests/test_sidecar.py`:

```python
import io
import json

import pytest

from vmaxpanel.metrics import UNAVAILABLE
from vmaxpanel.providers.msr import MsrProvider
from vmaxpanel.providers.registry import Registry
from vmaxpanel.providers.sidecar import SidecarClient
from vmaxpanel.providers.sidecar_providers import Gsa1Provider, LhmProvider, PdhProvider

SAMPLE = {
    "gsa1": {"cpu.temp": 42.0, "cpu.vrm_temp": 38.0, "cpu.vcore": 1.05},
    "pdh": {"cpu.clock": 4080, "cpu.name": "12th Gen Intel Core i5-12400F"},
    "lhm": {"gpu.name": "AMD Radeon RX 6800 XT", "gpu.load": 12.0,
            "gpu.temp": 51.0, "disk.temp.0": 34.0, "disk.temp.1": 40.0},
    "caps": {"gsa1": True, "pdh": True, "lhm": True},
}


class FakeProc:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(l + "\n" for l in lines))
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def poll(self):
        return None


def client_for(sample, caps_override=None):
    payload = dict(sample)
    if caps_override is not None:
        payload["caps"] = caps_override
    proc = FakeProc(["arrancando el sidecar", json.dumps(payload)])
    c = SidecarClient(script="ignored.ps1", spawn=lambda: proc)
    c.start()
    assert c.wait_ready(timeout=2.0)
    return c, proc


def test_client_ignores_non_json_lines_and_parses_the_sample():
    c, _ = client_for(SAMPLE)
    assert c.namespace("gsa1")["cpu.temp"] == 42.0
    assert c.caps() == {"gsa1": True, "pdh": True, "lhm": True}


def test_namespace_missing_returns_empty_dict():
    c, _ = client_for({"caps": {"gsa1": False}})
    assert c.namespace("lhm") == {}


def test_close_terminates_the_process():
    c, proc = client_for(SAMPLE)
    c.close()
    assert proc.terminated


def test_providers_serve_their_own_namespace_only():
    c, _ = client_for(SAMPLE)
    assert Gsa1Provider(c).metrics() == {"cpu.temp", "cpu.vrm_temp", "cpu.vcore"}
    assert PdhProvider(c).metrics() == {"cpu.clock", "cpu.name"}
    lhm = LhmProvider(c).metrics()
    assert {"gpu.load", "gpu.temp", "disk.temp.0", "disk.temp.1"} <= lhm
    assert "cpu.temp" not in lhm


def test_lhm_disk_metrics_are_discovered_from_the_sample():
    c, _ = client_for(SAMPLE)
    assert LhmProvider(c).read()["disk.temp.1"] == 40.0


def test_probe_follows_caps():
    c, _ = client_for(SAMPLE, caps_override={"gsa1": False, "pdh": True, "lhm": True})
    gsa = Gsa1Provider(c)
    assert gsa.probe() is False
    assert "Gigabyte" in gsa.unavailable_reason
    assert PdhProvider(c).probe() is True


def test_msr_provider_is_unavailable_with_a_real_reason():
    m = MsrProvider()
    assert m.id == "msr"
    assert m.probe() is False
    assert "WinRing0" in m.unavailable_reason
    assert m.metrics() == {"cpu.power", "cpu.fan"}


def test_registry_over_sidecar_marks_unserved_metrics_unavailable():
    c, _ = client_for(SAMPLE, caps_override={"gsa1": False, "pdh": True, "lhm": True})
    r = Registry([Gsa1Provider(c), PdhProvider(c), LhmProvider(c), MsrProvider()])
    sample = r.read()
    assert sample["cpu.temp"] is UNAVAILABLE
    assert sample["cpu.power"] is UNAVAILABLE
    assert sample["cpu.clock"] == 4080
    assert "Gigabyte" in r.unavailable()["cpu.temp"]
    assert "WinRing0" in r.unavailable()["cpu.power"]


def test_stale_client_reports_not_fresh(monkeypatch):
    c, _ = client_for(SAMPLE)
    assert c.fresh
    monkeypatch.setattr("vmaxpanel.providers.sidecar.time.time", lambda: 1e12)
    assert not c.fresh
```

- [ ] **Step 4: Correr y verificar que fallan**

Run: `python -m pytest tests/test_sidecar.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel.providers.sidecar'`

- [ ] **Step 5: Implementar el cliente del sidecar**

`vmaxpanel/providers/sidecar.py`:

```python
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
```

- [ ] **Step 6: Implementar los tres providers del sidecar**

`vmaxpanel/providers/sidecar_providers.py`:

```python
"""Providers que leen del mismo SidecarClient, cada uno su namespace."""
from .base import Provider


class _SidecarProvider(Provider):
    namespace = "?"
    served: set[str] = set()
    reason = "el sidecar no reporto esta capacidad"

    def __init__(self, client):
        self._c = client

    def probe(self) -> bool:
        if not self._c.caps().get(self.namespace, False):
            self.unavailable_reason = self.reason
            return False
        self.unavailable_reason = None
        return True

    def metrics(self) -> set[str]:
        return set(self.served)

    def read(self):
        return self._c.namespace(self.namespace)


class Gsa1Provider(_SidecarProvider):
    id = "gsa1"
    namespace = "gsa1"
    served = {"cpu.temp", "cpu.vrm_temp", "cpu.vcore"}
    reason = ("requiere placa Gigabyte con la interfaz ACPI-WMI GSA1 "
              "(clase GSA1_ACPIMethod)")


class PdhProvider(_SidecarProvider):
    id = "pdh"
    namespace = "pdh"
    served = {"cpu.clock", "cpu.name"}
    reason = "no se pudo leer el contador PDH % Processor Performance"


class LhmProvider(_SidecarProvider):
    """GPU y temps de SSD. Los ids de disco se descubren de la muestra."""

    id = "lhm"
    namespace = "lhm"
    _FIXED = {"gpu.name", "gpu.load", "gpu.temp", "gpu.hotspot",
              "gpu.clock", "gpu.power", "gpu.vram", "gpu.fan"}
    reason = ("no se pudo abrir LibreHardwareMonitor "
              "(falta LibreHardwareMonitorLib.dll o HidSharp.dll al lado)")

    @property
    def served(self):
        disks = {k for k in self._c.namespace("lhm") if k.startswith("disk.temp.")}
        return self._FIXED | disks

    def metrics(self) -> set[str]:
        return set(self.served)
```

`LhmProvider.served` es dinámico porque la cantidad de discos depende de la máquina. El registry lee `metrics()` en el constructor, así que el sidecar tiene que haber entregado su primera muestra antes de armar el `Registry` — de eso se encarga `wait_ready()` en el engine (Task 11).

- [ ] **Step 7: Implementar el provider MSR**

`vmaxpanel/providers/msr.py`:

```python
"""Package power y fan RPM de CPU: necesitan MSR via driver ring0.

En esta maquina WinRing0 esta bloqueado: StartService devuelve 0xE1
(ERROR_VIRUS_INFECTED) porque el driver esta en la blocklist de drivers
vulnerables de Windows. No se intenta cargarlo.

El provider existe igual para que el editor pueda explicar POR QUE la metrica
no esta, en vez de mostrar "--" sin motivo. En una maquina donde el driver
cargue, aca es donde se implementa la lectura.
"""
from .base import Provider


class MsrProvider(Provider):
    id = "msr"

    def probe(self) -> bool:
        self.unavailable_reason = (
            "requiere acceso a MSR por driver ring0 (WinRing0), bloqueado por "
            "la blocklist de drivers vulnerables de Windows")
        return False

    def metrics(self) -> set[str]:
        return {"cpu.power", "cpu.fan"}

    def read(self):
        return {}
```

- [ ] **Step 8: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_sidecar.py -v`
Expected: PASS, 9 tests

- [ ] **Step 9: Correr toda la suite**

Run: `python -m pytest -v`
Expected: PASS, 21 tests

- [ ] **Step 10: Commit**

```bash
git add vmaxpanel/sensors.ps1 vmaxpanel/providers/sidecar.py vmaxpanel/providers/sidecar_providers.py vmaxpanel/providers/msr.py tests/test_sidecar.py
git commit -m "feat: sidecar con ids canonicos y capabilities; providers gsa1/pdh/lhm/msr"
```

---

### Task 4: Modelo de layout y validador

El validador es propio, sin `jsonschema`: da errores atados al modelo y es una dependencia menos para distribuir. Devuelve **la lista completa** de errores, no el primero — el editor los muestra todos juntos.

**Files:**
- Create: `vmaxpanel/layout/__init__.py`, `vmaxpanel/layout/model.py`, `vmaxpanel/layout/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: `vmaxpanel.metrics.{is_metric, spec_for}`
- Produces:
  - `model.Layout(version, name, designed_for: Size, panel: PanelCfg, fonts: dict[str, Font], background: Background, widgets: list[Widget])`
  - `model.Size(width: int, height: int)`, `model.PanelCfg(rotate, brightness, fps, jpeg_quality)`, `model.Font(family, size, bold)`, `model.Rule(op, value, color)`, `model.Background(type, **fields)`
  - Widgets: `TextWidget`, `LabelWidget`, `BarWidget`, `ArcWidget`, `GraphWidget`, `ImageWidget`, todos con `id, type, x, y`.
  - `schema.SUPPORTED_VERSION = 1`, `schema.validate(raw: dict) -> list[str]`, `schema.build(raw: dict) -> Layout` (asume ya validado), `schema.WIDGET_TYPES: dict[str, type]`, `schema.safe_asset_path(src: str) -> str | None`.

- [ ] **Step 1: Escribir el test que falla**

`tests/test_schema.py`:

```python
import copy

from vmaxpanel.layout import model, schema

MINIMAL = {
    "version": 1,
    "name": "Test",
    "designed_for": {"width": 320, "height": 1480},
    "panel": {"rotate": 180, "brightness": 100, "fps": 1, "jpeg_quality": 82},
    "fonts": {"mono-14": {"family": "Consolas", "size": 14},
              "mono-bold-60": {"family": "Consolas", "size": 60, "bold": True}},
    "background": {"type": "solid", "color": "#0F1218"},
    "widgets": [
        {"id": "hdr", "type": "label", "text": "CPU", "x": 24, "y": 230,
         "font": "mono-14", "color": "#898781"},
        {"id": "load", "type": "text", "metric": "cpu.load", "x": 20, "y": 248,
         "font": "mono-bold-60", "color": "#FFFFFF", "format": "{:.1f}%",
         "rules": [{"when": "> 85", "color": "#FF4444"}]},
        {"id": "bar", "type": "bar", "metric": "cpu.load", "x": 24, "y": 316,
         "w": 272, "h": 16, "radius": 5, "fill": "#3987E5", "track": "#242834"},
    ],
}


def broken(**changes):
    raw = copy.deepcopy(MINIMAL)
    raw.update(changes)
    return raw


def with_widget(w):
    raw = copy.deepcopy(MINIMAL)
    raw["widgets"] = [w]
    return raw


def test_minimal_layout_is_valid():
    assert schema.validate(MINIMAL) == []


def test_build_returns_typed_model():
    lay = schema.build(MINIMAL)
    assert isinstance(lay, model.Layout)
    assert lay.designed_for == model.Size(320, 1480)
    assert lay.panel.rotate == 180
    assert lay.fonts["mono-bold-60"].bold is True
    assert isinstance(lay.widgets[0], model.LabelWidget)
    assert isinstance(lay.widgets[1], model.TextWidget)
    assert isinstance(lay.widgets[2], model.BarWidget)
    assert lay.widgets[1].rules[0] == model.Rule(">", 85.0, "#FF4444")


def test_future_version_is_rejected_clearly():
    errs = schema.validate(broken(version=schema.SUPPORTED_VERSION + 1))
    assert any("version" in e and "soportada" in e for e in errs)


def test_unknown_metric_is_named_in_the_error():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "text", "metric": "cpu.powr", "x": 0, "y": 0,
         "font": "mono-14", "color": "#FFFFFF", "format": "{:.0f}"}))
    assert any("cpu.powr" in e for e in errs)


def test_unknown_font_alias_is_named():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "label", "text": "x", "x": 0, "y": 0,
         "font": "no-existe", "color": "#FFFFFF"}))
    assert any("no-existe" in e for e in errs)


def test_duplicate_widget_ids_are_rejected():
    raw = copy.deepcopy(MINIMAL)
    raw["widgets"][1]["id"] = "hdr"
    assert any("hdr" in e and "repetido" in e for e in schema.validate(raw))


def test_bad_colors_are_rejected():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "label", "text": "x", "x": 0, "y": 0,
         "font": "mono-14", "color": "rojo"}))
    assert any("color" in e for e in errs)


def test_rotate_must_be_a_quarter_turn():
    assert any("rotate" in e for e in schema.validate(
        broken(panel={"rotate": 45, "brightness": 100, "fps": 1, "jpeg_quality": 82})))


def test_brightness_and_quality_ranges():
    errs = schema.validate(broken(
        panel={"rotate": 0, "brightness": 400, "fps": 1, "jpeg_quality": 200}))
    assert any("brightness" in e for e in errs)
    assert any("jpeg_quality" in e for e in errs)


def test_format_must_have_exactly_one_field():
    def fmt(f):
        return schema.validate(with_widget(
            {"id": "w", "type": "text", "metric": "cpu.load", "x": 0, "y": 0,
             "font": "mono-14", "color": "#FFFFFF", "format": f}))

    assert fmt("{:.1f}%") == []
    assert any("format" in e for e in fmt("sin campos"))
    assert any("format" in e for e in fmt("{:.0f} {:.0f}"))
    assert any("format" in e for e in fmt("{load}"))


def test_rule_operator_must_be_a_comparator():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "text", "metric": "cpu.load", "x": 0, "y": 0,
         "font": "mono-14", "color": "#FFFFFF", "format": "{:.0f}",
         "rules": [{"when": "os.system('calc')", "color": "#FF0000"}]}))
    assert any("when" in e for e in errs)


def test_asset_paths_cannot_escape_the_assets_dir():
    assert schema.safe_asset_path("logos/mio.png") == "logos/mio.png"
    assert schema.safe_asset_path("sub/../ok.png") == "ok.png"
    for bad in ("../../windows/system32/config/sam", "C:\\Windows\\win.ini",
                "/etc/passwd", "\\\\server\\share\\x.png", ".."):
        assert schema.safe_asset_path(bad) is None, bad


def test_image_widget_with_escaping_src_is_rejected():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "image", "src": "..\\..\\secreto.png",
         "x": 0, "y": 0, "w": 32, "h": 32}))
    assert any("src" in e for e in errs)


def test_unknown_widget_type_is_rejected():
    errs = schema.validate(with_widget({"id": "w", "type": "hologram", "x": 0, "y": 0}))
    assert any("hologram" in e for e in errs)


def test_missing_required_field_is_named():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "bar", "metric": "cpu.load", "x": 0, "y": 0}))
    assert any("w" in e for e in errs) and any("h" in e for e in errs)


def test_background_types_are_checked():
    assert schema.validate(broken(background={"type": "plasma"}))
    assert schema.validate(broken(
        background={"type": "gradient",
                    "stops": [{"at": 0.0, "color": "#000000"},
                              {"at": 1.0, "color": "#101418"}],
                    "angle": 90})) == []
    assert any("src" in e for e in schema.validate(
        broken(background={"type": "image", "src": "../fuera.png"})))


def test_disk_metric_by_index_is_accepted():
    assert schema.validate(with_widget(
        {"id": "w", "type": "text", "metric": "disk.temp.2", "x": 0, "y": 0,
         "font": "mono-14", "color": "#FFFFFF", "format": "{:.0f}"})) == []


def test_errors_accumulate_instead_of_stopping_at_the_first():
    raw = broken(version=99, background={"type": "plasma"})
    raw["widgets"] = [{"id": "w", "type": "hologram", "x": 0, "y": 0}]
    assert len(schema.validate(raw)) >= 3
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_schema.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel.layout'`

- [ ] **Step 3: Implementar el modelo**

`vmaxpanel/layout/__init__.py`:

```python
```

`vmaxpanel/layout/model.py`:

```python
"""Modelo tipado de un layout. Puramente declarativo: nada aca se ejecuta."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Size:
    width: int
    height: int


@dataclass
class PanelCfg:
    rotate: int = 0
    brightness: int = 100
    fps: float = 1.0
    jpeg_quality: int = 82


@dataclass(frozen=True)
class Font:
    family: str
    size: int
    bold: bool = False


@dataclass(frozen=True)
class Rule:
    op: str          # ">" | ">=" | "<" | "<="
    value: float
    color: str

    def matches(self, v) -> bool:
        if not isinstance(v, (int, float)):
            return False
        if self.op == ">":
            return v > self.value
        if self.op == ">=":
            return v >= self.value
        if self.op == "<":
            return v < self.value
        return v <= self.value


@dataclass
class Background:
    type: str = "solid"
    color: str = "#000000"
    stops: list = field(default_factory=list)
    angle: float = 90.0
    src: str | None = None
    fit: str = "cover"


@dataclass
class Widget:
    id: str
    type: str
    x: int
    y: int


@dataclass
class TextWidget(Widget):
    metric: str = ""
    font: str = ""
    color: str = "#FFFFFF"
    format: str = "{}"
    align: str = "left"
    rules: list[Rule] = field(default_factory=list)


@dataclass
class LabelWidget(Widget):
    text: str = ""
    font: str = ""
    color: str = "#FFFFFF"
    align: str = "left"


@dataclass
class BarWidget(Widget):
    metric: str = ""
    w: int = 0
    h: int = 0
    radius: int = 0
    fill: str = "#3987E5"
    track: str = "#242834"
    min: float | None = None
    max: float | None = None


@dataclass
class ArcWidget(Widget):
    metric: str = ""
    r: int = 0
    thickness: int = 8
    start_angle: float = 135.0
    sweep: float = 270.0
    fill: str = "#3987E5"
    track: str = "#242834"
    min: float | None = None
    max: float | None = None


@dataclass
class GraphWidget(Widget):
    metric: str = ""
    w: int = 0
    h: int = 0
    color: str = "#3987E5"
    track: str = "#242834"
    samples: int = 120
    min: float | None = None
    max: float | None = None


@dataclass
class ImageWidget(Widget):
    src: str = ""
    w: int = 0
    h: int = 0


@dataclass
class Layout:
    version: int
    name: str
    designed_for: Size
    panel: PanelCfg
    fonts: dict[str, Font]
    background: Background
    widgets: list[Widget]

    def font_for(self, alias: str) -> Font:
        return self.fonts[alias]
```

- [ ] **Step 4: Implementar el validador**

`vmaxpanel/layout/schema.py`:

```python
"""Validador propio de layouts.

Devuelve la lista completa de errores en castellano llano, para que el editor
los muestre todos juntos. No usa jsonschema: los mensajes quedan atados a
nuestro modelo y es una dependencia menos para distribuir.

Los layouts se comparten entre usuarios, asi que un layout NO puede ejecutar
nada: las reglas de color son comparadores parseados a mano, no expresiones.
"""
import posixpath
import re
from string import Formatter

from ..metrics import is_metric
from .model import (ArcWidget, Background, BarWidget, Font, GraphWidget,
                    ImageWidget, LabelWidget, Layout, PanelCfg, Rule, Size,
                    TextWidget, Widget)

SUPPORTED_VERSION = 1

WIDGET_TYPES = {
    "text": TextWidget, "label": LabelWidget, "bar": BarWidget,
    "arc": ArcWidget, "graph": GraphWidget, "image": ImageWidget,
}

BACKGROUND_TYPES = {"solid", "gradient", "image", "sequence", "video", "procedural"}
ALIGNS = {"left", "center", "right"}
FITS = {"cover", "contain", "stretch"}
ROTATIONS = {0, 90, 180, 270}

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_RULE_RE = re.compile(r"^\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

# campos obligatorios ademas de id/type/x/y
REQUIRED = {
    "text": ["metric", "font", "color", "format"],
    "label": ["text", "font", "color"],
    "bar": ["metric", "w", "h"],
    "arc": ["metric", "r"],
    "graph": ["metric", "w", "h"],
    "image": ["src", "w", "h"],
}


def safe_asset_path(src) -> str | None:
    """Normaliza una ruta de asset y la rechaza si se escapa del directorio.

    El servicio corre como SYSTEM: sin esto, un '..\\..\\' le hace leer
    cualquier archivo de la maquina.
    """
    if not isinstance(src, str) or not src.strip():
        return None
    s = src.replace("\\", "/")
    if s.startswith("/") or s.startswith("//") or re.match(r"^[A-Za-z]:", s):
        return None
    norm = posixpath.normpath(s)
    if norm == "." or norm == ".." or norm.startswith("../"):
        return None
    return norm


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_color(errs, where, v):
    if not isinstance(v, str) or not _COLOR_RE.match(v):
        errs.append(f"{where}: color invalido {v!r}, se espera #RRGGBB")


def _check_format(errs, where, v):
    if not isinstance(v, str):
        errs.append(f"{where}: format debe ser texto")
        return
    fields = [f for _, f, _, _ in Formatter().parse(v) if f is not None]
    if len(fields) != 1:
        errs.append(f"{where}: format {v!r} debe tener exactamente un campo, "
                    f"tiene {len(fields)}")
    elif fields[0] not in ("", "0"):
        errs.append(f"{where}: format {v!r} no puede nombrar el campo "
                    f"({fields[0]!r}); use {{}} o {{0}}")


def _parse_rule(raw):
    if not isinstance(raw, dict):
        return None
    m = _RULE_RE.match(str(raw.get("when", "")))
    if not m:
        return None
    return Rule(m.group(1), float(m.group(2)), raw.get("color", "#FFFFFF"))


def validate(raw) -> list[str]:
    errs: list[str] = []
    if not isinstance(raw, dict):
        return ["el layout debe ser un objeto JSON"]

    v = raw.get("version")
    if not _is_int(v):
        errs.append("version: falta o no es entero")
    elif v > SUPPORTED_VERSION:
        errs.append(f"version {v} es mayor que la soportada ({SUPPORTED_VERSION}); "
                    f"actualiza VMax Panel")
    elif v < 1:
        errs.append(f"version {v} invalida")

    if not isinstance(raw.get("name"), str) or not raw.get("name"):
        errs.append("name: falta o esta vacio")

    df = raw.get("designed_for")
    if not isinstance(df, dict) or not _is_int(df.get("width")) or not _is_int(df.get("height")):
        errs.append("designed_for: se esperan width y height enteros")
    elif df["width"] <= 0 or df["height"] <= 0:
        errs.append("designed_for: width y height deben ser positivos")

    p = raw.get("panel")
    if not isinstance(p, dict):
        errs.append("panel: falta")
    else:
        if p.get("rotate", 0) not in ROTATIONS:
            errs.append(f"panel.rotate: {p.get('rotate')!r} invalido, "
                        f"se espera 0, 90, 180 o 270")
        b = p.get("brightness", 100)
        if not _is_int(b) or not 0 <= b <= 100:
            errs.append(f"panel.brightness: {b!r} fuera de 0..100")
        f = p.get("fps", 1.0)
        if not _is_num(f) or not 0.1 <= f <= 30:
            errs.append(f"panel.fps: {f!r} fuera de 0.1..30")
        q = p.get("jpeg_quality", 82)
        if not _is_int(q) or not 30 <= q <= 95:
            errs.append(f"panel.jpeg_quality: {q!r} fuera de 30..95")

    fonts = raw.get("fonts")
    if not isinstance(fonts, dict) or not fonts:
        errs.append("fonts: falta la tabla de alias de fuente")
        fonts = {}
    else:
        for alias, spec in fonts.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("family"), str):
                errs.append(f"fonts.{alias}: falta family")
            elif not _is_int(spec.get("size")) or spec["size"] <= 0:
                errs.append(f"fonts.{alias}: size debe ser entero positivo")

    bg = raw.get("background")
    if not isinstance(bg, dict) or bg.get("type") not in BACKGROUND_TYPES:
        errs.append(f"background.type: {bg.get('type') if isinstance(bg, dict) else bg!r} "
                    f"invalido, se espera uno de {sorted(BACKGROUND_TYPES)}")
    else:
        t = bg["type"]
        if t == "solid":
            _check_color(errs, "background", bg.get("color"))
        elif t == "gradient":
            stops = bg.get("stops")
            if not isinstance(stops, list) or len(stops) < 2:
                errs.append("background.stops: se esperan al menos dos paradas")
            else:
                for i, s in enumerate(stops):
                    if not isinstance(s, dict) or not _is_num(s.get("at")):
                        errs.append(f"background.stops[{i}]: falta at numerico")
                    elif not 0.0 <= s["at"] <= 1.0:
                        errs.append(f"background.stops[{i}]: at fuera de 0..1")
                    _check_color(errs, f"background.stops[{i}]", s.get("color")
                                 if isinstance(s, dict) else None)
        elif t in ("image", "sequence", "video"):
            if safe_asset_path(bg.get("src")) is None:
                errs.append(f"background.src: ruta invalida o fuera del directorio "
                            f"de assets: {bg.get('src')!r}")
            if bg.get("fit", "cover") not in FITS:
                errs.append(f"background.fit: {bg.get('fit')!r} invalido")

    widgets = raw.get("widgets")
    if not isinstance(widgets, list):
        errs.append("widgets: se espera una lista")
        return errs

    seen = set()
    for i, w in enumerate(widgets):
        errs.extend(_validate_widget(w, i, fonts, seen))
    return errs


def _validate_widget(w, i, fonts, seen) -> list[str]:
    errs = []
    if not isinstance(w, dict):
        return [f"widgets[{i}]: se espera un objeto"]

    wid = w.get("id")
    where = f"widget {wid!r}" if isinstance(wid, str) and wid else f"widgets[{i}]"
    if not isinstance(wid, str) or not wid:
        errs.append(f"widgets[{i}]: falta id")
    elif wid in seen:
        errs.append(f"{where}: id repetido")
    else:
        seen.add(wid)

    t = w.get("type")
    if t not in WIDGET_TYPES:
        return errs + [f"{where}: tipo desconocido {t!r}, se espera uno de "
                       f"{sorted(WIDGET_TYPES)}"]

    for k in ("x", "y"):
        if not _is_int(w.get(k)):
            errs.append(f"{where}: {k} debe ser entero")

    for k in REQUIRED[t]:
        if k not in w:
            errs.append(f"{where}: falta el campo obligatorio {k!r}")

    if "metric" in REQUIRED[t] and "metric" in w and not is_metric(w["metric"]):
        errs.append(f"{where}: metrica desconocida {w['metric']!r}")

    if "font" in REQUIRED[t] and isinstance(w.get("font"), str) and w["font"] not in fonts:
        errs.append(f"{where}: alias de fuente desconocido {w['font']!r}")

    for k in ("color", "fill", "track"):
        if k in w:
            _check_color(errs, where, w[k])

    if w.get("align", "left") not in ALIGNS:
        errs.append(f"{where}: align {w.get('align')!r} invalido")

    if t == "text":
        if "format" in w:
            _check_format(errs, where, w["format"])
        for j, r in enumerate(w.get("rules") or []):
            if _parse_rule(r) is None:
                errs.append(f"{where}: rules[{j}].when invalido "
                            f"{r.get('when') if isinstance(r, dict) else r!r}; "
                            f"se espera un comparador como '> 85'")
            elif isinstance(r, dict):
                _check_color(errs, f"{where} rules[{j}]", r.get("color"))

    for k in ("w", "h", "r", "thickness", "radius", "samples"):
        if k in w and not _is_int(w[k]):
            errs.append(f"{where}: {k} debe ser entero")

    if t == "image" and "src" in w and safe_asset_path(w["src"]) is None:
        errs.append(f"{where}: src invalido o fuera del directorio de assets: "
                    f"{w['src']!r}")

    return errs


def build(raw) -> Layout:
    """Construye el modelo. Asume que validate(raw) devolvio []."""
    fonts = {a: Font(s["family"], s["size"], bool(s.get("bold", False)))
             for a, s in raw["fonts"].items()}
    bgr = raw["background"]
    bg = Background(
        type=bgr["type"],
        color=bgr.get("color", "#000000"),
        stops=[{"at": float(s["at"]), "color": s["color"]} for s in bgr.get("stops", [])],
        angle=float(bgr.get("angle", 90.0)),
        src=safe_asset_path(bgr["src"]) if bgr.get("src") else None,
        fit=bgr.get("fit", "cover"))

    widgets: list[Widget] = []
    for w in raw["widgets"]:
        cls = WIDGET_TYPES[w["type"]]
        kwargs = {k: v for k, v in w.items() if k in cls.__dataclass_fields__}
        if cls is TextWidget:
            kwargs["rules"] = [r for r in (_parse_rule(x) for x in w.get("rules") or [])
                               if r is not None]
        if cls is ImageWidget:
            kwargs["src"] = safe_asset_path(w["src"])
        widgets.append(cls(**kwargs))

    p = raw["panel"]
    return Layout(
        version=raw["version"],
        name=raw["name"],
        designed_for=Size(raw["designed_for"]["width"], raw["designed_for"]["height"]),
        panel=PanelCfg(p.get("rotate", 0), p.get("brightness", 100),
                       float(p.get("fps", 1.0)), p.get("jpeg_quality", 82)),
        fonts=fonts, background=bg, widgets=widgets)
```

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS, 18 tests

- [ ] **Step 6: Commit**

```bash
git add vmaxpanel/layout tests/test_schema.py
git commit -m "feat: modelo de layout y validador propio con rutas de asset confinadas"
```

---

### Task 5: Cargador de perfiles con semántica de mantener-el-anterior

Un JSON roto no puede dejar el panel en negro. `ProfileStore` mantiene el último layout válido y reporta los errores del intento fallido.

**Files:**
- Create: `vmaxpanel/layout/loader.py`
- Test: `tests/test_loader.py`

**Interfaces:**
- Consumes: `schema.{validate, build, SUPPORTED_VERSION}`, `model.Layout`
- Produces: `LayoutError(Exception)` con atributo `errors: list[str]`; `loads(text) -> Layout`; `load(path) -> Layout`; `save(layout, path)`; `ProfileStore(path)` con `current: Layout | None`, `load_now() -> list[str]`, `reload_if_changed() -> tuple[bool, list[str]]`.

- [ ] **Step 1: Escribir el test que falla**

`tests/test_loader.py`:

```python
import json

import pytest

from vmaxpanel.layout import loader, model
from tests.test_schema import MINIMAL


def write(tmp_path, obj, name="p.json"):
    path = tmp_path / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_loads_valid_layout():
    lay = loader.loads(json.dumps(MINIMAL))
    assert isinstance(lay, model.Layout)
    assert lay.name == "Test"


def test_loads_invalid_json_raises_with_errors():
    with pytest.raises(loader.LayoutError) as e:
        loader.loads("{no es json")
    assert e.value.errors


def test_loads_invalid_layout_lists_every_error():
    bad = dict(MINIMAL, version=99, background={"type": "plasma"})
    with pytest.raises(loader.LayoutError) as e:
        loader.loads(json.dumps(bad))
    assert len(e.value.errors) >= 2


def test_save_then_load_roundtrips(tmp_path):
    path = tmp_path / "out.json"
    loader.save(loader.loads(json.dumps(MINIMAL)), path)
    again = loader.load(path)
    assert again.name == "Test"
    assert again.widgets[1].rules[0].value == 85.0
    assert again.panel.rotate == 180


def test_store_keeps_previous_layout_when_reload_fails(tmp_path):
    path = write(tmp_path, MINIMAL)
    store = loader.ProfileStore(path)
    assert store.load_now() == []
    good = store.current
    assert good.name == "Test"

    path.write_text("{roto", encoding="utf-8")
    changed, errors = store.reload_if_changed()
    assert changed is False
    assert errors
    assert store.current is good        # el panel sigue mostrando el layout bueno


def test_store_reloads_when_the_file_changes(tmp_path):
    path = write(tmp_path, MINIMAL)
    store = loader.ProfileStore(path)
    store.load_now()
    write(tmp_path, dict(MINIMAL, name="Otro"), name="p.json")
    changed, errors = store.reload_if_changed()
    assert changed is True and errors == []
    assert store.current.name == "Otro"


def test_store_reports_no_change_when_untouched(tmp_path):
    store = loader.ProfileStore(write(tmp_path, MINIMAL))
    store.load_now()
    assert store.reload_if_changed() == (False, [])


def test_store_on_missing_file_reports_error_without_raising(tmp_path):
    store = loader.ProfileStore(tmp_path / "no-existe.json")
    errors = store.load_now()
    assert errors and store.current is None
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_loader.py -v`
Expected: FAIL con `AttributeError: module 'vmaxpanel.layout.loader' has no attribute ...` / ImportError

- [ ] **Step 3: Implementar**

`vmaxpanel/layout/loader.py`:

```python
"""Carga, guardado y recarga en caliente de perfiles.

Invariante: un layout invalido NUNCA reemplaza al que esta andando. El panel no
se queda negro por un JSON mal escrito.
"""
import json
import os
from dataclasses import asdict, is_dataclass

from . import schema
from .model import Layout


class LayoutError(Exception):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def loads(text: str) -> Layout:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise LayoutError([f"JSON invalido: {e}"]) from None
    errors = schema.validate(raw)
    if errors:
        raise LayoutError(errors)
    return schema.build(raw)


def load(path) -> Layout:
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        raise LayoutError([f"no se pudo leer {path}: {e}"]) from None
    return loads(text)


def to_dict(layout: Layout) -> dict:
    d = asdict(layout)
    d["designed_for"] = {"width": layout.designed_for.width,
                         "height": layout.designed_for.height}
    d["fonts"] = {a: {"family": f.family, "size": f.size, "bold": f.bold}
                  for a, f in layout.fonts.items()}
    bg = {"type": layout.background.type}
    if layout.background.type == "solid":
        bg["color"] = layout.background.color
    elif layout.background.type == "gradient":
        bg["stops"] = layout.background.stops
        bg["angle"] = layout.background.angle
    else:
        bg["src"] = layout.background.src
        bg["fit"] = layout.background.fit
    d["background"] = bg
    d["widgets"] = [_widget_dict(w) for w in layout.widgets]
    return d


def _widget_dict(w) -> dict:
    d = {k: v for k, v in asdict(w).items() if not _is_default(w, k, v)}
    d["id"], d["type"], d["x"], d["y"] = w.id, w.type, w.x, w.y
    if getattr(w, "rules", None):
        d["rules"] = [{"when": f"{r.op} {r.value:g}", "color": r.color} for r in w.rules]
    return d


def _is_default(w, key, value):
    f = type(w).__dataclass_fields__.get(key)
    if f is None or key in ("id", "type", "x", "y"):
        return False
    if key == "rules":
        return True                     # se reescribe en _widget_dict
    default = f.default
    return default is not None and not is_dataclass(default) and value == default


def save(layout: Layout, path):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(to_dict(layout), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)               # atomico: nunca se lee un archivo a medias


class ProfileStore:
    """Mantiene el layout activo y lo recarga cuando el archivo cambia."""

    def __init__(self, path):
        self.path = path
        self.current: Layout | None = None
        self.errors: list[str] = []
        self._mtime = None

    def load_now(self) -> list[str]:
        try:
            self.current = load(self.path)
            self.errors = []
        except LayoutError as e:
            self.errors = e.errors
        self._mtime = self._stat()
        return self.errors

    def _stat(self):
        try:
            return os.stat(self.path).st_mtime_ns
        except OSError:
            return None

    def reload_if_changed(self) -> tuple[bool, list[str]]:
        mtime = self._stat()
        if mtime == self._mtime:
            return False, []
        self._mtime = mtime
        try:
            new = load(self.path)
        except LayoutError as e:
            self.errors = e.errors
            return False, e.errors      # se mantiene self.current
        self.current, self.errors = new, []
        return True, []
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_loader.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Correr toda la suite**

Run: `python -m pytest`
Expected: PASS, 47 tests

- [ ] **Step 6: Commit**

```bash
git add vmaxpanel/layout/loader.py tests/test_loader.py
git commit -m "feat: cargador de perfiles con recarga en caliente y mantener-el-anterior"
```

---

### Task 6: Resolución de fuentes por familia, con fallback

No se empaquetan TTFs — Consolas es de Microsoft. Las fuentes se buscan por **nombre de familia**: primero en `vmaxpanel/assets/fonts/` (vacío en fase 1, es donde la fase 3 pondrá una mono libre), después entre las del sistema. Un layout que pide una familia ausente cae a la mono empaquetada o a la default de PIL, **nunca crashea**.

**Files:**
- Create: `vmaxpanel/render/__init__.py`, `vmaxpanel/render/fonts.py`
- Create: `vmaxpanel/assets/fonts/.gitkeep`
- Test: `tests/test_fonts.py`

**Interfaces:**
- Consumes: `model.Font`
- Produces: `FontResolver(extra_dirs: list[Path] | None = None)` con `resolve(font: model.Font, scale: float = 1.0) -> PIL.ImageFont.FreeTypeFont`, `missing() -> set[str]`, y `index() -> dict[str, dict[str, Path]]` (familia en minúsculas → estilo → ruta).

- [ ] **Step 1: Escribir el test que falla**

`tests/test_fonts.py`:

```python
from PIL import ImageFont

from vmaxpanel.layout.model import Font
from vmaxpanel.render.fonts import FontResolver


def test_resolves_a_system_font_by_family():
    r = FontResolver()
    f = r.resolve(Font("Consolas", 20))
    assert isinstance(f, ImageFont.FreeTypeFont)
    assert "consolas" in f.getname()[0].lower()
    assert not r.missing()


def test_bold_variant_differs_from_regular():
    r = FontResolver()
    reg = r.resolve(Font("Consolas", 40, bold=False))
    bold = r.resolve(Font("Consolas", 40, bold=True))
    assert reg.getlength("MMMM") != bold.getlength("MMMM")


def test_missing_family_falls_back_and_is_reported():
    r = FontResolver()
    f = r.resolve(Font("NoExisteEstaFuente", 20))
    assert f is not None
    assert "NoExisteEstaFuente" in r.missing()


def test_scale_multiplies_the_size():
    r = FontResolver()
    small = r.resolve(Font("Consolas", 20), scale=1.0)
    big = r.resolve(Font("Consolas", 20), scale=2.0)
    assert big.getlength("M") > small.getlength("M") * 1.5


def test_scale_never_produces_a_zero_size():
    assert FontResolver().resolve(Font("Consolas", 8), scale=0.01) is not None


def test_resolution_is_cached():
    r = FontResolver()
    assert r.resolve(Font("Consolas", 20)) is r.resolve(Font("Consolas", 20))


def test_extra_dirs_are_indexed(tmp_path):
    """Una fuente en extra_dirs se encuentra por su familia real, no por el archivo."""
    r = FontResolver()
    src = r.index()["consolas"]["regular"]
    (tmp_path / "copia.ttf").write_bytes(src.read_bytes())
    r2 = FontResolver(extra_dirs=[tmp_path])
    assert r2.index()["consolas"]["regular"] == tmp_path / "copia.ttf"
    assert not r2.missing()
```

El índice usa la familia que declara el archivo (`Consolas`), no el nombre del archivo, así que la copia gana por precedencia de directorio.

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_fonts.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel.render'`

- [ ] **Step 3: Implementar**

`vmaxpanel/render/__init__.py`:

```python
```

`vmaxpanel/render/fonts.py`:

```python
"""Resuelve alias de fuente a archivos reales, por nombre de familia.

No empaquetamos TTFs: consola.ttf/consolab.ttf son Consolas, de Microsoft, y no
son redistribuibles. Se busca por familia en assets/fonts/ (donde la fase 3
pondra una mono libre) y despues entre las fuentes del sistema.

Una familia ausente cae al fallback y se anota en missing(), para que el editor
lo pueda avisar. Nunca lanza: un layout ajeno no puede tumbar el render.
"""
import os
from pathlib import Path

from PIL import ImageFont

BUNDLED = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_EXTS = (".ttf", ".otf", ".ttc")
_BOLD_HINTS = ("bold", "bd", "black", "heavy", "semibold")


def _system_font_dirs() -> list[Path]:
    dirs = []
    windir = os.environ.get("WINDIR")
    if windir:
        dirs.append(Path(windir) / "Fonts")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return [d for d in dirs if d.is_dir()]


class FontResolver:
    def __init__(self, extra_dirs=None):
        # orden de precedencia: extra > empaquetadas > sistema
        self._dirs = [Path(d) for d in (extra_dirs or [])] + [BUNDLED] + _system_font_dirs()
        self._index: dict[str, dict[str, Path]] | None = None
        self._cache: dict[tuple, ImageFont.FreeTypeFont] = {}
        self._missing: set[str] = set()

    def index(self):
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def _build_index(self):
        idx: dict[str, dict[str, Path]] = {}
        for d in self._dirs:
            for path in sorted(d.iterdir()):
                if path.suffix.lower() not in _EXTS or not path.is_file():
                    continue
                family, style = self._names(path)
                if not family:
                    continue
                slot = "bold" if self._is_bold(style, path.stem) else "regular"
                idx.setdefault(family.lower(), {}).setdefault(slot, path)
        return idx

    @staticmethod
    def _names(path):
        try:
            f = ImageFont.truetype(os.fspath(path), 12)
            family, style = f.getname()
            return family or path.stem, style or ""
        except Exception:
            return None, ""

    @staticmethod
    def _is_bold(style, stem):
        text = f"{style} {stem}".lower()
        return any(h in text for h in _BOLD_HINTS)

    def missing(self) -> set[str]:
        return set(self._missing)

    def resolve(self, font, scale: float = 1.0):
        size = max(1, int(round(font.size * scale)))
        key = (font.family.lower(), size, font.bold)
        if key in self._cache:
            return self._cache[key]

        entry = self.index().get(font.family.lower())
        path = None
        if entry:
            path = entry.get("bold" if font.bold else "regular") or next(iter(entry.values()))
        else:
            self._missing.add(font.family)
            fallback = self._first_bundled() or self._any_system_mono()
            path = fallback

        try:
            resolved = ImageFont.truetype(os.fspath(path), size) if path \
                else ImageFont.load_default(size)
        except Exception:
            resolved = ImageFont.load_default(size)

        self._cache[key] = resolved
        return resolved

    def _first_bundled(self):
        if not BUNDLED.is_dir():
            return None
        for p in sorted(BUNDLED.iterdir()):
            if p.suffix.lower() in _EXTS:
                return p
        return None

    def _any_system_mono(self):
        for family in ("consolas", "cascadia mono", "courier new", "dejavu sans mono"):
            entry = self.index().get(family)
            if entry:
                return entry.get("regular") or next(iter(entry.values()))
        return None
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_fonts.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
mkdir -p vmaxpanel/assets/fonts && touch vmaxpanel/assets/fonts/.gitkeep
git add vmaxpanel/render vmaxpanel/assets/fonts/.gitkeep tests/test_fonts.py
git commit -m "feat: resolucion de fuentes por familia sin empaquetar TTFs"
```

---

### Task 7: Dibujo de widgets

Seis funciones de dibujo, una por tipo. Todas reciben la escala y ninguna asume la geometría del panel. `UNAVAILABLE` y `None` se dibujan igual (`--`), pero el editor los distingue por el registry.

**Files:**
- Create: `vmaxpanel/render/widgets.py`
- Test: `tests/test_widgets.py`

**Interfaces:**
- Consumes: `model.*Widget`, `metrics.UNAVAILABLE`, `FontResolver`
- Produces:
  - `DASH = "--"`
  - `format_value(widget: TextWidget, value) -> str`
  - `color_for(widget: TextWidget, value) -> str`
  - `draw(img: Image.Image, widget: Widget, value, ctx: DrawCtx) -> None` — despacha por tipo.
  - `DrawCtx(fonts: FontResolver, layout: Layout, scale: float, assets_dir: Path, history: dict[str, list[float]])`

- [ ] **Step 1: Escribir el test que falla**

`tests/test_widgets.py`:

```python
from pathlib import Path

import pytest
from PIL import Image

from vmaxpanel.layout import model
from vmaxpanel.metrics import UNAVAILABLE
from vmaxpanel.render import widgets
from vmaxpanel.render.fonts import FontResolver

FONTS = {"m": model.Font("Consolas", 20), "big": model.Font("Consolas", 60, bold=True)}


def ctx(scale=1.0, history=None, assets_dir=None):
    layout = model.Layout(1, "t", model.Size(320, 1480), model.PanelCfg(),
                          FONTS, model.Background(), [])
    return widgets.DrawCtx(fonts=FontResolver(), layout=layout, scale=scale,
                           assets_dir=assets_dir or Path("."),
                           history=history or {})


def canvas(w=320, h=200):
    return Image.new("RGB", (w, h), (0, 0, 0))


def text_widget(**kw):
    base = dict(id="w", type="text", x=10, y=10, metric="cpu.load", font="m",
                color="#FFFFFF", format="{:.1f}%")
    base.update(kw)
    return model.TextWidget(**base)


def test_format_value_formats_numbers():
    assert widgets.format_value(text_widget(), 12.34) == "12.3%"


def test_format_value_dashes_none_and_unavailable():
    w = text_widget()
    assert widgets.format_value(w, None) == "--%"
    assert widgets.format_value(w, UNAVAILABLE) == "--%"


def test_format_value_keeps_the_suffix_outside_the_field():
    w = text_widget(format="{:.0f} MHz")
    assert widgets.format_value(w, 4080) == "4080 MHz"
    assert widgets.format_value(w, None) == "-- MHz"


def test_format_value_passes_text_metrics_through():
    w = text_widget(metric="cpu.name", format="{}")
    assert widgets.format_value(w, "INTEL CORE i5") == "INTEL CORE i5"


def test_format_value_survives_a_type_mismatch():
    w = text_widget(format="{:.1f}")
    assert widgets.format_value(w, "no numerico") == "--"


def test_color_for_applies_the_first_matching_rule():
    w = text_widget(rules=[model.Rule(">", 85.0, "#FF4444"),
                           model.Rule(">", 60.0, "#FFAA00")])
    assert widgets.color_for(w, 40.0) == "#FFFFFF"
    assert widgets.color_for(w, 70.0) == "#FFAA00"
    assert widgets.color_for(w, 90.0) == "#FF4444"


def test_color_for_ignores_rules_on_unavailable():
    w = text_widget(rules=[model.Rule(">", 85.0, "#FF4444")])
    assert widgets.color_for(w, UNAVAILABLE) == "#FFFFFF"


def test_draw_text_puts_ink_on_the_canvas():
    im = canvas()
    widgets.draw(im, text_widget(), 55.5, ctx())
    assert im.getbbox() is not None


def test_align_shifts_the_text_left():
    left, right = canvas(), canvas()
    widgets.draw(left, text_widget(x=160, align="left"), 55.5, ctx())
    widgets.draw(right, text_widget(x=160, align="right"), 55.5, ctx())
    assert right.getbbox()[0] < left.getbbox()[0]


def test_draw_label_needs_no_metric():
    im = canvas()
    widgets.draw(im, model.LabelWidget(id="l", type="label", x=5, y=5, text="CPU",
                                       font="m", color="#898781"), None, ctx())
    assert im.getbbox() is not None


def test_bar_fill_grows_with_the_value():
    def width_at(pct):
        im = canvas()
        w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                            w=272, h=16, radius=5, fill="#3987E5", track="#242834")
        widgets.draw(im, w, pct, ctx())
        px = im.load()
        return sum(1 for x in range(320) if px[x, 18] == (57, 135, 229))

    assert width_at(25) < width_at(75)


def test_bar_with_unavailable_draws_only_the_track():
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                        w=272, h=16, fill="#3987E5", track="#242834")
    widgets.draw(im, w, UNAVAILABLE, ctx())
    px = im.load()
    assert (57, 135, 229) not in [px[x, 18] for x in range(320)]
    assert im.getbbox() is not None          # el track si se dibuja


def test_bar_clamps_out_of_range_values():
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                        w=100, h=16, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 500.0, ctx())
    px = im.load()
    assert px[109, 18] == (57, 135, 229)
    assert px[200, 18] == (0, 0, 0)          # no se pasa del ancho


def test_bar_uses_metric_range_when_min_max_absent():
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=0, y=10, metric="cpu.vcore",
                        w=100, h=16, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 1.0, ctx())          # cpu.vcore va 0..2 -> mitad
    px = im.load()
    assert px[40, 18] == (57, 135, 229)
    assert px[80, 18] != (57, 135, 229)


def test_arc_draws_something():
    im = canvas()
    w = model.ArcWidget(id="a", type="arc", x=100, y=100, metric="cpu.load", r=40,
                        thickness=8, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 50.0, ctx())
    assert im.getbbox() is not None


def test_graph_uses_history():
    im = canvas()
    w = model.GraphWidget(id="g", type="graph", x=10, y=10, metric="cpu.load",
                          w=200, h=60, color="#3987E5", samples=10)
    widgets.draw(im, w, 50.0, ctx(history={"cpu.load": [10, 30, 90, 20, 60]}))
    assert im.getbbox() is not None


def test_graph_with_empty_history_does_not_crash():
    im = canvas()
    w = model.GraphWidget(id="g", type="graph", x=10, y=10, metric="cpu.load",
                          w=200, h=60, color="#3987E5")
    widgets.draw(im, w, None, ctx(history={}))


def test_image_widget_is_skipped_when_the_asset_is_missing(tmp_path):
    im = canvas()
    w = model.ImageWidget(id="i", type="image", x=0, y=0, src="no-existe.png",
                          w=32, h=32)
    widgets.draw(im, w, None, ctx(assets_dir=tmp_path))
    assert im.getbbox() is None              # no dibujo nada, y no exploto


def test_image_widget_draws_an_existing_asset(tmp_path):
    Image.new("RGB", (8, 8), (255, 0, 0)).save(tmp_path / "logo.png")
    im = canvas()
    w = model.ImageWidget(id="i", type="image", x=4, y=4, src="logo.png", w=16, h=16)
    widgets.draw(im, w, None, ctx(assets_dir=tmp_path))
    assert im.getbbox() is not None


def test_scale_moves_and_grows_a_bar():
    im = canvas(640, 400)
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                        w=100, h=16, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 100.0, ctx(scale=2.0))
    px = im.load()
    assert px[150, 36] == (57, 135, 229)     # x*2=20, w*2=200 -> 20..220
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_widgets.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel.render.widgets'`

- [ ] **Step 3: Implementar**

`vmaxpanel/render/widgets.py`:

```python
"""Dibujo de cada tipo de widget.

Todo recibe `scale` y nadie asume la geometria del panel: un layout disenado
para 320x1480 se dibuja igual en otro tamano.

Un valor ausente se dibuja como "--". UNAVAILABLE (nadie sirve la metrica) y
None (el provider no trajo dato esta vuelta) se ven igual en el panel; el editor
los distingue consultando el registry.
"""
import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from ..layout import model
from ..metrics import UNAVAILABLE, spec_for

DASH = "--"


@dataclass
class DrawCtx:
    fonts: object
    layout: model.Layout
    scale: float = 1.0
    assets_dir: Path = Path(".")
    history: dict = field(default_factory=dict)


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def format_value(w: model.TextWidget, value) -> str:
    """Aplica w.format, o deja "--" conservando el sufijo del template."""
    if value is None or value is UNAVAILABLE:
        return _dashed(w.format)
    try:
        return w.format.format(value)
    except (ValueError, TypeError):
        return _dashed(w.format)


def _dashed(fmt: str) -> str:
    """"{:.0f} MHz" -> "-- MHz": reemplaza el campo por DASH sin perder el resto."""
    try:
        return fmt.format(_Dash())
    except Exception:
        return DASH


class _Dash:
    def __format__(self, spec):
        return DASH


def color_for(w: model.TextWidget, value) -> str:
    v = _num(value)
    if v is not None:
        for rule in w.rules:
            if rule.matches(v):
                return rule.color
    return w.color


def draw(img: Image.Image, w: model.Widget, value, ctx: DrawCtx) -> None:
    fn = _DISPATCH.get(w.type)
    if fn is None:
        return
    fn(img, ImageDraw.Draw(img), w, value, ctx)


def _s(ctx, v):
    return int(round(v * ctx.scale))


def _anchored(draw_obj, x, y, text, font, align):
    if align == "left":
        return x, y
    length = draw_obj.textlength(text, font=font)
    return (x - length / 2 if align == "center" else x - length), y


def _draw_text(img, g, w, value, ctx):
    font = ctx.fonts.resolve(ctx.layout.fonts[w.font], ctx.scale)
    text = format_value(w, value)
    x, y = _anchored(g, _s(ctx, w.x), _s(ctx, w.y), text, font, w.align)
    g.text((x, y), text, font=font, fill=color_for(w, value))


def _draw_label(img, g, w, value, ctx):
    font = ctx.fonts.resolve(ctx.layout.fonts[w.font], ctx.scale)
    x, y = _anchored(g, _s(ctx, w.x), _s(ctx, w.y), w.text, font, w.align)
    g.text((x, y), w.text, font=font, fill=w.color)


def _range(w):
    lo, hi = w.min, w.max
    if lo is None or hi is None:
        spec = spec_for(w.metric)
        if spec is not None:
            lo = spec.min if lo is None else lo
            hi = spec.max if hi is None else hi
    return (0.0 if lo is None else lo), (100.0 if hi is None else hi)


def _fraction(w, value):
    v = _num(value)
    if v is None:
        return None
    lo, hi = _range(w)
    if hi <= lo:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _draw_bar(img, g, w, value, ctx):
    x, y = _s(ctx, w.x), _s(ctx, w.y)
    ww, hh = _s(ctx, w.w), _s(ctx, w.h)
    radius = _s(ctx, getattr(w, "radius", 0))
    g.rounded_rectangle([x, y, x + ww, y + hh], radius=radius, fill=w.track)
    frac = _fraction(w, value)
    if frac is None:
        return
    fw = int(ww * frac)
    if fw > 2:
        g.rounded_rectangle([x, y, x + fw, y + hh], radius=radius, fill=w.fill)


def _draw_arc(img, g, w, value, ctx):
    r, t = _s(ctx, w.r), max(1, _s(ctx, w.thickness))
    cx, cy = _s(ctx, w.x), _s(ctx, w.y)
    box = [cx - r, cy - r, cx + r, cy + r]
    g.arc(box, w.start_angle, w.start_angle + w.sweep, fill=w.track, width=t)
    frac = _fraction(w, value)
    if frac:
        g.arc(box, w.start_angle, w.start_angle + w.sweep * frac, fill=w.fill, width=t)


def _draw_graph(img, g, w, value, ctx):
    x, y = _s(ctx, w.x), _s(ctx, w.y)
    ww, hh = _s(ctx, w.w), _s(ctx, w.h)
    if w.track:
        g.rectangle([x, y, x + ww, y + hh], fill=w.track)
    series = list(ctx.history.get(w.metric) or [])[-w.samples:]
    if len(series) < 2:
        return
    lo, hi = _range(w)
    span = (hi - lo) or 1.0
    step = ww / (len(series) - 1)
    pts = []
    for i, v in enumerate(series):
        n = _num(v)
        frac = 0.0 if n is None else max(0.0, min(1.0, (n - lo) / span))
        pts.append((x + i * step, y + hh - frac * hh))
    g.line(pts, fill=w.color, width=max(1, _s(ctx, 2)))


def _draw_image(img, g, w, value, ctx):
    if not w.src:
        return
    path = Path(ctx.assets_dir) / w.src
    try:
        asset = Image.open(path).convert("RGBA")
    except Exception:
        return                          # asset faltante: se omite el widget
    size = (max(1, _s(ctx, w.w)), max(1, _s(ctx, w.h)))
    img.paste(asset.resize(size, Image.LANCZOS), (_s(ctx, w.x), _s(ctx, w.y)),
              asset.resize(size, Image.LANCZOS))


_DISPATCH = {
    "text": _draw_text, "label": _draw_label, "bar": _draw_bar,
    "arc": _draw_arc, "graph": _draw_graph, "image": _draw_image,
}
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_widgets.py -v`
Expected: PASS, 20 tests

- [ ] **Step 5: Commit**

```bash
git add vmaxpanel/render/widgets.py tests/test_widgets.py
git commit -m "feat: dibujo de los seis tipos de widget con escala y reglas de color"
```

---

### Task 8: Fondos estáticos

Fase 1 cubre `solid`, `gradient` e `image`. `sequence`, `video` y `procedural` son fase 2 y por ahora caen a `solid` con un motivo reportado, en vez de fallar. El fondo se cachea: es el mismo en cada frame mientras el layout no cambie.

**Files:**
- Create: `vmaxpanel/render/background.py`
- Test: `tests/test_background.py`

**Interfaces:**
- Consumes: `model.Background`, `model.Size`
- Produces: `BackgroundSource(bg: model.Background, size: model.Size, assets_dir: Path)` con `frame() -> Image.Image` (RGB del tamaño pedido), `warnings: list[str]`, `animated: bool` (False en toda la fase 1).

- [ ] **Step 1: Escribir el test que falla**

`tests/test_background.py`:

```python
from PIL import Image

from vmaxpanel.layout import model
from vmaxpanel.render.background import BackgroundSource

SIZE = model.Size(64, 200)


def src(bg, assets_dir="."):
    return BackgroundSource(bg, SIZE, assets_dir)


def test_solid_fills_the_whole_frame():
    im = src(model.Background(type="solid", color="#0F1218")).frame()
    assert im.size == (64, 200)
    assert im.mode == "RGB"
    assert im.getpixel((0, 0)) == im.getpixel((63, 199)) == (15, 18, 24)


def test_gradient_changes_along_the_axis():
    bg = model.Background(type="gradient", angle=90.0, stops=[
        {"at": 0.0, "color": "#000000"}, {"at": 1.0, "color": "#FFFFFF"}])
    im = src(bg).frame()
    top, bottom = im.getpixel((32, 0)), im.getpixel((32, 199))
    assert sum(top) < sum(bottom)
    assert im.getpixel((0, 100)) == im.getpixel((63, 100))   # 90 grados = vertical


def test_gradient_at_zero_degrees_is_horizontal():
    bg = model.Background(type="gradient", angle=0.0, stops=[
        {"at": 0.0, "color": "#000000"}, {"at": 1.0, "color": "#FFFFFF"}])
    im = src(bg).frame()
    assert sum(im.getpixel((0, 100))) < sum(im.getpixel((63, 100)))
    assert im.getpixel((32, 0)) == im.getpixel((32, 199))


def test_gradient_honours_intermediate_stops():
    bg = model.Background(type="gradient", angle=90.0, stops=[
        {"at": 0.0, "color": "#000000"},
        {"at": 0.5, "color": "#FF0000"},
        {"at": 1.0, "color": "#000000"}])
    im = src(bg).frame()
    assert im.getpixel((32, 100))[0] > 200
    assert im.getpixel((32, 0))[0] < 20


def test_image_cover_fills_without_letterboxing(tmp_path):
    Image.new("RGB", (10, 10), (200, 40, 40)).save(tmp_path / "b.png")
    im = src(model.Background(type="image", src="b.png", fit="cover"), tmp_path).frame()
    assert im.size == (64, 200)
    assert im.getpixel((0, 0)) == (200, 40, 40)


def test_image_contain_letterboxes(tmp_path):
    Image.new("RGB", (10, 10), (200, 40, 40)).save(tmp_path / "b.png")
    im = src(model.Background(type="image", src="b.png", fit="contain"), tmp_path).frame()
    assert im.getpixel((32, 0)) == (0, 0, 0)          # banda arriba
    assert im.getpixel((32, 100)) == (200, 40, 40)


def test_missing_image_degrades_to_solid_with_a_warning(tmp_path):
    s = src(model.Background(type="image", src="no-existe.png"), tmp_path)
    assert s.frame().size == (64, 200)
    assert any("no-existe.png" in w for w in s.warnings)


def test_phase2_types_degrade_with_a_warning():
    for t in ("sequence", "video", "procedural"):
        s = src(model.Background(type=t, src="x.mp4"))
        assert s.frame().size == (64, 200)
        assert any("fase 2" in w for w in s.warnings), t


def test_frame_is_cached_and_returns_a_copy():
    s = src(model.Background(type="solid", color="#101010"))
    a, b = s.frame(), s.frame()
    assert a is not b                                  # mutar uno no ensucia el cache
    assert list(a.getdata()) == list(b.getdata())


def test_animated_is_false_in_phase_one():
    assert src(model.Background(type="solid")).animated is False
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_background.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel.render.background'`

- [ ] **Step 3: Implementar**

`vmaxpanel/render/background.py`:

```python
"""Fondos. Fase 1: solid, gradient e image.

sequence/video/procedural son fase 2 y degradan a solid con un aviso, en vez de
fallar: un perfil compartido que los use tiene que seguir abriendo.

El fondo se cachea porque no cambia entre frames mientras el layout sea el mismo;
el loop de render solo copia el cache y le dibuja los widgets encima.
"""
from pathlib import Path

from PIL import Image

FALLBACK = (10, 12, 16)
PHASE2 = {"sequence", "video", "procedural"}


def parse_hex(color, default=FALLBACK):
    if not isinstance(color, str) or not color.startswith("#") or len(color) != 7:
        return default
    try:
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return default


class BackgroundSource:
    animated = False        # fase 2 lo pone en True para sequence/video/procedural

    def __init__(self, bg, size, assets_dir="."):
        self.bg = bg
        self.size = (size.width, size.height)
        self.assets_dir = Path(assets_dir)
        self.warnings: list[str] = []
        self._cache = None

    def frame(self) -> Image.Image:
        if self._cache is None:
            self._cache = self._build()
        return self._cache.copy()

    def _build(self) -> Image.Image:
        t = self.bg.type
        if t in PHASE2:
            self.warnings.append(
                f"fondo de tipo {t!r} todavia no esta implementado (fase 2); "
                f"se usa un color plano")
            return self._solid()
        if t == "gradient":
            return self._gradient()
        if t == "image":
            return self._image()
        return self._solid()

    def _solid(self):
        return Image.new("RGB", self.size, parse_hex(self.bg.color))

    def _gradient(self):
        stops = sorted(self.bg.stops, key=lambda s: s["at"])
        if len(stops) < 2:
            return self._solid()
        vertical = 45 <= (self.bg.angle % 180) < 135
        n = self.size[1] if vertical else self.size[0]
        line = Image.new("RGB", (1, n) if vertical else (n, 1))
        px = line.load()
        for i in range(n):
            c = self._sample(stops, i / max(1, n - 1))
            px[(0, i) if vertical else (i, 0)] = c
        return line.resize(self.size, Image.BILINEAR)

    @staticmethod
    def _sample(stops, t):
        if t <= stops[0]["at"]:
            return parse_hex(stops[0]["color"])
        if t >= stops[-1]["at"]:
            return parse_hex(stops[-1]["color"])
        for a, b in zip(stops, stops[1:]):
            if a["at"] <= t <= b["at"]:
                span = (b["at"] - a["at"]) or 1.0
                k = (t - a["at"]) / span
                ca, cb = parse_hex(a["color"]), parse_hex(b["color"])
                return tuple(int(round(ca[i] + (cb[i] - ca[i]) * k)) for i in range(3))
        return parse_hex(stops[-1]["color"])

    def _image(self):
        if not self.bg.src:
            self.warnings.append("fondo de tipo 'image' sin src")
            return self._solid()
        path = self.assets_dir / self.bg.src
        try:
            src = Image.open(path).convert("RGB")
        except Exception as e:
            self.warnings.append(f"no se pudo abrir el fondo {self.bg.src!r}: {e}")
            return self._solid()
        return self._fit(src)

    def _fit(self, src):
        tw, th = self.size
        if self.bg.fit == "stretch":
            return src.resize(self.size, Image.LANCZOS)
        sw, sh = src.size
        k = max(tw / sw, th / sh) if self.bg.fit == "cover" else min(tw / sw, th / sh)
        scaled = src.resize((max(1, int(sw * k)), max(1, int(sh * k))), Image.LANCZOS)
        out = Image.new("RGB", self.size, parse_hex(self.bg.color, (0, 0, 0)))
        out.paste(scaled, ((tw - scaled.width) // 2, (th - scaled.height) // 2))
        return out
```

Nota: `_solid()` con un `Background` de tipo `image` usa `bg.color`, que por defecto es `#000000` — de ahí las bandas negras del test de `contain`.

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_background.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add vmaxpanel/render/background.py tests/test_background.py
git commit -m "feat: fondos solid/gradient/image con degradado avisado para fase 2"
```

---

### Task 9: Renderer

Compone el frame: fondo cacheado, escala si el panel real difiere de `designed_for`, widgets encima. Es el módulo que en la fase 3 importa también el editor, así que su interfaz tiene que aguantar los dos usos.

**Files:**
- Create: `vmaxpanel/render/renderer.py`
- Test: `tests/test_renderer.py`

**Interfaces:**
- Consumes: `Layout`, `FontResolver`, `BackgroundSource`, `widgets.draw`
- Produces:
  - `Renderer(layout: Layout, panel_size: Size | None = None, assets_dir: Path = ...)` con `frame(sample: dict, history: dict | None = None) -> Image.Image`, `set_layout(layout)`, `scale: float`, `warnings() -> list[str]`.
  - `to_jpeg(img, rotate: int = 0, quality: int = 82) -> bytes`
  - `History(maxlen: int = 320)` con `push(sample: dict)`, `series() -> dict[str, list[float]]`

- [ ] **Step 1: Escribir el test que falla**

`tests/test_renderer.py`:

```python
import io

from PIL import Image

from vmaxpanel.layout import model, schema
from vmaxpanel.metrics import UNAVAILABLE
from vmaxpanel.render.renderer import History, Renderer, to_jpeg
from tests.test_schema import MINIMAL

SAMPLE = {"cpu.load": 55.5}


def layout(**over):
    raw = dict(MINIMAL)
    raw.update(over)
    return schema.build(raw)


def test_frame_has_the_designed_size_by_default():
    im = Renderer(layout()).frame(SAMPLE)
    assert im.size == (320, 1480)
    assert im.mode == "RGB"


def test_frame_scales_uniformly_to_the_real_panel():
    r = Renderer(layout(), panel_size=model.Size(640, 2960))
    assert r.scale == 2.0
    assert r.frame(SAMPLE).size == (640, 2960)


def test_scale_uses_the_smaller_axis_and_centers():
    r = Renderer(layout(), panel_size=model.Size(320, 740))
    assert r.scale == 0.5
    assert r.frame(SAMPLE).size == (320, 740)


def test_widgets_are_drawn_over_the_background():
    lay = layout(background={"type": "solid", "color": "#000000"})
    im = Renderer(lay).frame(SAMPLE)
    assert im.getbbox() is not None          # el fondo negro no cuenta como tinta


def test_unavailable_metric_renders_dashes_without_crashing():
    im = Renderer(layout()).frame({"cpu.load": UNAVAILABLE})
    assert im.size == (320, 1480)


def test_empty_sample_renders_a_full_frame():
    assert Renderer(layout()).frame({}).size == (320, 1480)


def test_set_layout_rebuilds_the_background_cache():
    r = Renderer(layout(background={"type": "solid", "color": "#FF0000"}))
    assert r.frame({}).getpixel((5, 5)) == (255, 0, 0)
    r.set_layout(layout(background={"type": "solid", "color": "#00FF00"}))
    assert r.frame({}).getpixel((5, 5)) == (0, 255, 0)


def test_warnings_surface_missing_fonts_and_assets():
    lay = layout(fonts={"mono-14": {"family": "NoExiste", "size": 14},
                        "mono-bold-60": {"family": "NoExiste", "size": 60}})
    r = Renderer(lay)
    r.frame(SAMPLE)
    assert any("NoExiste" in w for w in r.warnings())


def test_to_jpeg_produces_a_baseline_jpeg():
    data = to_jpeg(Renderer(layout()).frame(SAMPLE), rotate=0, quality=82)
    assert data[:3] == b"\xff\xd8\xff"
    assert data[-2:] == b"\xff\xd9"
    assert Image.open(io.BytesIO(data)).size == (320, 1480)


def test_to_jpeg_rotation_swaps_the_axes_for_90():
    im = Renderer(layout()).frame(SAMPLE)
    assert Image.open(io.BytesIO(to_jpeg(im, rotate=90))).size == (1480, 320)
    assert Image.open(io.BytesIO(to_jpeg(im, rotate=180))).size == (320, 1480)


def test_lower_quality_produces_fewer_bytes():
    im = Renderer(layout()).frame(SAMPLE)
    assert len(to_jpeg(im, quality=50)) < len(to_jpeg(im, quality=90))


def test_history_keeps_only_numbers_and_respects_maxlen():
    h = History(maxlen=3)
    for v in (10, 20, UNAVAILABLE, 30, None, 40):
        h.push({"cpu.load": v})
    assert h.series()["cpu.load"] == [20, 30, 40]


def test_history_ignores_text_metrics():
    h = History()
    h.push({"cpu.name": "INTEL", "cpu.load": 5.0})
    assert "cpu.name" not in h.series()
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_renderer.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel.render.renderer'`

- [ ] **Step 3: Implementar**

`vmaxpanel/render/renderer.py`:

```python
"""Compone el frame del panel: fondo + widgets.

Un solo renderer para el servicio y para el editor. Si hubiera dos
implementaciones divergirian y el preview del editor terminaria mintiendo.

La escala es uniforme (min de los dos ejes) y se aplica tambien al tamano de
fuente: escalar los ejes por separado deformaria el texto.
"""
import io
from collections import defaultdict, deque
from pathlib import Path

from PIL import Image

from ..layout.model import Size
from . import widgets as W
from .background import BackgroundSource
from .fonts import FontResolver

DEFAULT_ASSETS = Path(__file__).resolve().parent.parent / "assets"

ROTATIONS = {
    0: None,
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}


class History:
    """Ventana deslizante por metrica, para los widgets de tipo graph."""

    def __init__(self, maxlen: int = 320):
        self.maxlen = maxlen
        self._d = defaultdict(lambda: deque(maxlen=maxlen))

    def push(self, sample: dict):
        for mid, v in sample.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self._d[mid].append(v)

    def series(self) -> dict:
        return {k: list(v) for k, v in self._d.items()}


class Renderer:
    def __init__(self, layout, panel_size=None, assets_dir=DEFAULT_ASSETS):
        self.assets_dir = Path(assets_dir)
        self._fonts = FontResolver()
        self._panel_size = panel_size
        self.set_layout(layout)

    def set_layout(self, layout):
        self.layout = layout
        target = self._panel_size or layout.designed_for
        d = layout.designed_for
        self.scale = min(target.width / d.width, target.height / d.height)
        self.size = Size(target.width, target.height)
        self._bg = BackgroundSource(layout.background, self.size, self.assets_dir)

    def set_panel_size(self, panel_size):
        self._panel_size = panel_size
        self.set_layout(self.layout)

    def warnings(self) -> list[str]:
        return list(self._bg.warnings) + [
            f"fuente no encontrada: {f}" for f in sorted(self._fonts.missing())]

    def frame(self, sample: dict, history: dict | None = None) -> Image.Image:
        img = self._bg.frame()
        ctx = W.DrawCtx(fonts=self._fonts, layout=self.layout, scale=self.scale,
                        assets_dir=self.assets_dir, history=history or {})
        for w in self.layout.widgets:
            metric = getattr(w, "metric", None)
            value = sample.get(metric) if metric else None
            W.draw(img, w, value, ctx)
        return img


def to_jpeg(img: Image.Image, rotate: int = 0, quality: int = 82) -> bytes:
    """JPEG baseline 4:2:0 crudo: es exactamente lo que el panel espera.

    El panel de esta maquina esta montado al revez, de ahi el rotate=180 del
    perfil. En otro gabinete puede ser 0.
    """
    transpose = ROTATIONS.get(rotate)
    if transpose is not None:
        img = img.transpose(transpose)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=2)
    return buf.getvalue()
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_renderer.py -v`
Expected: PASS, 13 tests

- [ ] **Step 5: Correr toda la suite**

Run: `python -m pytest`
Expected: PASS, 90 tests

- [ ] **Step 6: Commit**

```bash
git add vmaxpanel/render/renderer.py tests/test_renderer.py
git commit -m "feat: renderer compartido con escala uniforme e historial para graphs"
```

---

### Task 10: Transporte del panel

Autodetección por VID/PID, geometría parseada del SN, handshake, brillo y envío. `FakeTransport` permite testear el protocolo entero sin el panel enchufado — que es lo que hace posible que otro usuario contribuya sin tener el hardware.

**Files:**
- Create: `vmaxpanel/transport/__init__.py`, `vmaxpanel/transport/panel_link.py`
- Test: `tests/test_panel_link.py`

**Interfaces:**
- Consumes: `pyserial` (`serial.tools.list_ports`)
- Produces:
  - `VID = 0x33C3`, `PID = 0xF101`, `HANDSHAKE = b"\xf0\xa5\x5a\x0f"`, `SN_LEN = 26`
  - `find_panel_ports() -> list[str]`
  - `parse_geometry(sn: str) -> Size` — `"VMAXA170320*1480S261001155"` → `Size(320, 1480)`; si no matchea, `Size(320, 1480)`.
  - `brightness_cmd(v: int) -> bytes`
  - `PanelLink(transport)` con `open() -> str` (devuelve el SN), `geometry -> Size`, `set_brightness(v)`, `send_frame(jpeg: bytes)`, `close()`
  - `SerialTransport(port: str)` y `FakeTransport(sn: str = ..., fail_on_write: Exception | None = None)` con `writes: list[bytes]`
  - `PanelNotFound(Exception)`

- [ ] **Step 1: Escribir el test que falla**

`tests/test_panel_link.py`:

```python
import pytest

from vmaxpanel.layout.model import Size
from vmaxpanel.transport.panel_link import (HANDSHAKE, FakeTransport, PanelLink,
                                            brightness_cmd, parse_geometry)


def test_parse_geometry_from_the_real_serial_number():
    assert parse_geometry("VMAXA170320*1480S261001155") == Size(320, 1480)


def test_parse_geometry_handles_other_models():
    assert parse_geometry("VMAXB99480*1920S000000001") == Size(480, 1920)


def test_parse_geometry_falls_back_when_unparseable():
    for sn in ("", "basura", "VMAX***S1", None):
        assert parse_geometry(sn) == Size(320, 1480)


def test_brightness_command_frames_the_value():
    assert brightness_cmd(60) == bytes([0xAA, 0xBB, 60, 0xCC, 0xDD])


def test_brightness_command_clamps():
    assert brightness_cmd(-5)[2] == 0
    assert brightness_cmd(500)[2] == 100


def test_open_sends_the_handshake_and_returns_the_serial_number():
    t = FakeTransport()
    link = PanelLink(t)
    sn = link.open()
    assert t.writes[0] == HANDSHAKE
    assert sn == "VMAXA170320*1480S261001155"
    assert link.geometry == Size(320, 1480)


def test_set_brightness_writes_the_command():
    t = FakeTransport()
    link = PanelLink(t)
    link.open()
    link.set_brightness(40)
    assert t.writes[-1] == bytes([0xAA, 0xBB, 40, 0xCC, 0xDD])


def test_send_frame_writes_the_jpeg_verbatim():
    t = FakeTransport()
    link = PanelLink(t)
    link.open()
    jpeg = b"\xff\xd8\xff" + b"\x00" * 40 + b"\xff\xd9"
    link.send_frame(jpeg)
    assert t.writes[-1] == jpeg          # sin header ni framing propio


def test_send_frame_rejects_data_that_is_not_a_jpeg():
    link = PanelLink(FakeTransport())
    link.open()
    with pytest.raises(ValueError, match="JPEG"):
        link.send_frame(b"no soy un jpeg")


def test_send_frame_before_open_raises():
    with pytest.raises(RuntimeError, match="open"):
        PanelLink(FakeTransport()).send_frame(b"\xff\xd8\xff\xff\xd9")


def test_short_serial_number_read_raises():
    link = PanelLink(FakeTransport(sn="corto"))
    with pytest.raises(OSError, match="SN"):
        link.open()


def test_close_closes_the_transport():
    t = FakeTransport()
    PanelLink(t).close()
    assert t.closed


def test_write_failure_propagates_as_oserror():
    t = FakeTransport(fail_on_write=OSError("puerto tomado"))
    with pytest.raises(OSError):
        PanelLink(t).open()
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_panel_link.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel.transport'`

- [ ] **Step 3: Implementar**

`vmaxpanel/transport/__init__.py`:

```python
```

`vmaxpanel/transport/panel_link.py`:

```python
"""Protocolo del panel HL-VMAX.

Reverseado hookeando WriteFile en el proceso de LCD Control:

    open  \\\\.\\COMx              CDC; el baud es irrelevante
    TX    F0 A5 5A 0F            handshake
    RX    <SN ascii, 26 bytes>    "VMAXA170320*1480S261001155"
    TX    AA BB <brillo 0..100> CC DD
    TX    <JPEG>                  un write por frame, sin header ni framing

El puerto NO se hardcodea: se autodetecta por VID/PID, porque en otra maquina no
es COM3. La geometria sale del propio SN.
"""
import re

from ..layout.model import Size

VID, PID = 0x33C3, 0xF101
HANDSHAKE = bytes([0xF0, 0xA5, 0x5A, 0x0F])
SN_LEN = 26
DEFAULT_GEOMETRY = Size(320, 1480)

_GEOM_RE = re.compile(r"(\d{2,5})\s*\*\s*(\d{2,5})")


class PanelNotFound(Exception):
    pass


def find_panel_ports() -> list[str]:
    from serial.tools import list_ports
    return [p.device for p in list_ports.comports()
            if p.vid == VID and p.pid == PID]


def parse_geometry(sn) -> Size:
    if isinstance(sn, str):
        m = _GEOM_RE.search(sn)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if w > 0 and h > 0:
                return Size(w, h)
    return DEFAULT_GEOMETRY


def brightness_cmd(v: int) -> bytes:
    return bytes([0xAA, 0xBB, max(0, min(100, int(v))), 0xCC, 0xDD])


class SerialTransport:
    """pyserial detras de la interfaz minima que PanelLink necesita."""

    def __init__(self, port, timeout=1.5, write_timeout=8):
        import serial
        self._ser = serial.Serial(port, 9600, timeout=timeout,
                                  write_timeout=write_timeout)
        self.port = port

    def write(self, data):
        self._ser.write(data)
        self._ser.flush()

    def read(self, n):
        return self._ser.read(n)

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass


class FakeTransport:
    """Transporte de prueba: captura los writes y devuelve un SN fijo."""

    def __init__(self, sn="VMAXA170320*1480S261001155", fail_on_write=None):
        self.writes = []
        self.closed = False
        self._sn = sn.encode("ascii", "replace")
        self._fail = fail_on_write

    def write(self, data):
        if self._fail is not None:
            raise self._fail
        self.writes.append(bytes(data))

    def read(self, n):
        out, self._sn = self._sn[:n], self._sn[n:]
        return out

    def close(self):
        self.closed = True


class PanelLink:
    def __init__(self, transport):
        self._t = transport
        self.serial_number = None
        self.geometry = DEFAULT_GEOMETRY

    @classmethod
    def autodetect(cls, port=None):
        ports = [port] if port else find_panel_ports()
        if not ports:
            raise PanelNotFound(
                f"no se encontro un panel HL-VMAX (VID_{VID:04X}/PID_{PID:04X}). "
                f"Revisa que este conectado y que no lo tenga tomado LCD Control.")
        return cls(SerialTransport(ports[0]))

    def open(self) -> str:
        self._t.write(HANDSHAKE)
        raw = self._t.read(SN_LEN)
        if len(raw) < SN_LEN:
            raise OSError(f"el panel devolvio un SN corto ({len(raw)} de {SN_LEN} bytes); "
                          f"puede estar tomado por otro proceso")
        self.serial_number = raw.decode("ascii", "replace")
        self.geometry = parse_geometry(self.serial_number)
        return self.serial_number

    def set_brightness(self, v: int):
        self._t.write(brightness_cmd(v))

    def send_frame(self, jpeg: bytes):
        if self.serial_number is None:
            raise RuntimeError("hay que llamar a open() antes de mandar frames")
        if not (jpeg[:3] == b"\xff\xd8\xff" and jpeg[-2:] == b"\xff\xd9"):
            raise ValueError("el frame no es un JPEG completo "
                             "(tiene que abrir en FFD8FF y cerrar en FFD9)")
        self._t.write(jpeg)

    def close(self):
        self._t.close()
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_panel_link.py -v`
Expected: PASS, 13 tests

- [ ] **Step 5: Verificar contra el panel real**

Primero liberar el puerto: `daemon\stop.ps1`

```python
python -c "from vmaxpanel.transport.panel_link import PanelLink; l=PanelLink.autodetect(); print(l.open(), l.geometry); l.close()"
```

Expected: imprime `VMAXA170320*1480S261001155 Size(width=320, height=1480)`

- [ ] **Step 6: Commit**

```bash
git add vmaxpanel/transport tests/test_panel_link.py
git commit -m "feat: transporte del panel con autodeteccion VID/PID y geometria del SN"
```

---

### Task 11: Engine

El loop: muestra sensores a 1 Hz, renderiza al fps del layout, recarga el perfil cuando el archivo cambia, y reintenta el serial con backoff cuando el panel desaparece. El transporte se inyecta, así que el loop se testea sin hardware.

**Files:**
- Create: `vmaxpanel/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `ProfileStore`, `Registry`, `Renderer`, `History`, `to_jpeg`, `PanelLink`
- Produces:
  - `EngineConfig(profile_path, sample_period=1.0, reconnect_backoff=(1,2,5,10), max_iterations=None)`
  - `Engine(store, registry, config, link_factory, clock=time)` con `run()`, `stop()`, `state() -> dict`, `stats: dict`
  - `state()` devuelve `{"panel": "ok"|"desconectado", "profile": str, "sn": str|None, "fps": float, "resolution": dict, "unavailable": dict, "warnings": list, "frames": int, "last_error": str|None}`

- [ ] **Step 1: Escribir el test que falla**

`tests/test_engine.py`:

```python
import json

import pytest

from vmaxpanel.engine import Engine, EngineConfig
from vmaxpanel.layout import loader
from vmaxpanel.providers.base import Provider
from vmaxpanel.providers.registry import Registry
from vmaxpanel.transport.panel_link import FakeTransport, PanelLink
from tests.test_schema import MINIMAL


class FakeCpu(Provider):
    id = "psutil"

    def __init__(self, value=42.0):
        self.value = value
        self.reads = 0

    def probe(self):
        return True

    def metrics(self):
        return {"cpu.load"}

    def read(self):
        self.reads += 1
        return {"cpu.load": self.value}


class FakeClock:
    """Reloj virtual: el loop avanza sin dormir de verdad."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += max(0.0, s)


def profile(tmp_path, **over):
    raw = dict(MINIMAL)
    raw.update(over)
    path = tmp_path / "vitals.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def engine(tmp_path, transports=None, iterations=3, **over):
    path = profile(tmp_path, **over)
    store = loader.ProfileStore(path)
    store.load_now()
    made = []

    def factory():
        t = (transports or [FakeTransport()]).pop(0) if transports else FakeTransport()
        made.append(t)
        return PanelLink(t)

    clock = FakeClock()
    cfg = EngineConfig(profile_path=path, max_iterations=iterations)
    eng = Engine(store, Registry([FakeCpu()]), cfg, link_factory=factory, clock=clock)
    return eng, made, clock


def test_run_sends_one_frame_per_iteration(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=3)
    eng.run()
    frames = [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"]
    assert len(frames) == 3
    assert eng.state()["frames"] == 3


def test_run_handshakes_and_sets_brightness_once(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=3)
    eng.run()
    writes = made[0].writes
    assert writes[0] == b"\xf0\xa5\x5a\x0f"
    assert sum(1 for w in writes if w[:2] == b"\xaa\xbb") == 1


def test_state_reports_the_panel_and_the_profile(tmp_path):
    eng, _, _ = engine(tmp_path, iterations=1)
    eng.run()
    st = eng.state()
    assert st["panel"] == "ok"
    assert st["profile"] == "Test"
    assert st["sn"].startswith("VMAX")
    assert st["resolution"]["cpu.load"] == "psutil"


def test_state_lists_unavailable_metrics_with_reasons(tmp_path):
    from vmaxpanel.providers.msr import MsrProvider
    path = profile(tmp_path)
    store = loader.ProfileStore(path)
    store.load_now()
    eng = Engine(store, Registry([FakeCpu(), MsrProvider()]),
                 EngineConfig(profile_path=path, max_iterations=1),
                 link_factory=lambda: PanelLink(FakeTransport()), clock=FakeClock())
    eng.run()
    assert "WinRing0" in eng.state()["unavailable"]["cpu.power"]


def test_frame_rate_respects_the_layout_fps(tmp_path):
    eng, _, clock = engine(tmp_path, iterations=4,
                           panel={"rotate": 0, "brightness": 100, "fps": 2,
                                  "jpeg_quality": 82})
    start = clock.now
    eng.run()
    assert 1.4 <= clock.now - start <= 1.6      # 3 esperas de 0.5 s


def test_sensors_are_sampled_once_per_period_not_once_per_frame(tmp_path):
    path = profile(tmp_path, panel={"rotate": 0, "brightness": 100, "fps": 4,
                                    "jpeg_quality": 82})
    store = loader.ProfileStore(path)
    store.load_now()
    cpu = FakeCpu()
    eng = Engine(store, Registry([cpu]),
                 EngineConfig(profile_path=path, sample_period=1.0, max_iterations=8),
                 link_factory=lambda: PanelLink(FakeTransport()), clock=FakeClock())
    eng.run()
    assert cpu.reads <= 4          # 8 frames a 4 fps = 2 s => 2-3 muestras, no 8


def test_layout_change_is_picked_up_without_restarting(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=4)
    path = tmp_path / "vitals.json"

    original_run = eng._render_once

    def patched():
        if eng.stats["frames"] == 1:
            path.write_text(json.dumps(dict(MINIMAL, name="Recargado")),
                            encoding="utf-8")
        return original_run()

    eng._render_once = patched
    eng.run()
    assert eng.state()["profile"] == "Recargado"


def test_broken_layout_on_reload_keeps_rendering_the_previous_one(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=4)
    path = tmp_path / "vitals.json"

    original_run = eng._render_once

    def patched():
        if eng.stats["frames"] == 1:
            path.write_text("{roto", encoding="utf-8")
        return original_run()

    eng._render_once = patched
    eng.run()
    st = eng.state()
    assert st["profile"] == "Test"           # sigue el bueno
    assert st["frames"] == 4                 # y no dejo de dibujar
    assert any("JSON" in w for w in st["warnings"])


def test_serial_failure_reconnects_with_backoff(tmp_path):
    dead = FakeTransport(fail_on_write=OSError("puerto tomado"))
    alive = FakeTransport()
    eng, made, clock = engine(tmp_path, transports=[dead, alive], iterations=2)
    start = clock.now
    eng.run()
    assert len(made) == 2
    assert clock.now > start                  # durmio el backoff
    assert eng.state()["panel"] == "ok"


def test_stop_ends_the_loop(tmp_path):
    eng, _, _ = engine(tmp_path, iterations=None)
    original = eng._render_once

    def patched():
        original()
        if eng.stats["frames"] >= 2:
            eng.stop()

    eng._render_once = patched
    eng.run()
    assert eng.stats["frames"] == 2


def test_jpeg_quality_and_rotation_come_from_the_profile(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=1,
                          panel={"rotate": 90, "brightness": 100, "fps": 1,
                                 "jpeg_quality": 40})
    eng.run()
    import io
    from PIL import Image
    frame = [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"][0]
    assert Image.open(io.BytesIO(frame)).size == (1480, 320)
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'vmaxpanel.engine'`

- [ ] **Step 3: Implementar**

`vmaxpanel/engine.py`:

```python
"""Loop de render.

Cadencias separadas: los sensores se muestrean cada `sample_period` (1 s por
defecto) y los frames salen al fps del layout. En la fase 2, con fondos
animados, esa separacion es lo que permite un fondo a 10 fps con datos a 1 Hz
sin releer sensores 10 veces por segundo.

El transporte se inyecta (`link_factory`), asi que el loop entero se testea con
FakeTransport, sin el panel enchufado.
"""
import time
from dataclasses import dataclass, field

from .render.renderer import History, Renderer, to_jpeg
from .transport.panel_link import PanelNotFound


@dataclass
class EngineConfig:
    profile_path: str
    sample_period: float = 1.0
    reconnect_backoff: tuple = (1.0, 2.0, 5.0, 10.0)
    max_iterations: int | None = None       # None = para siempre; los tests lo acotan
    history_len: int = 320


class Engine:
    def __init__(self, store, registry, config, link_factory, clock=time):
        self.store = store
        self.registry = registry
        self.cfg = config
        self._link_factory = link_factory
        self._clock = clock
        self._stop = False
        self._link = None
        self._renderer = None
        self._history = History(config.history_len)
        self._sample = {}
        self._last_sample_at = 0.0
        self._last_error = None
        self.stats = {"frames": 0, "reconnects": 0}

    # --- ciclo de vida ---

    def stop(self):
        self._stop = True

    def state(self) -> dict:
        layout = self.store.current
        return {
            "panel": "ok" if self._link is not None else "desconectado",
            "profile": layout.name if layout else None,
            "sn": self._link.serial_number if self._link else None,
            "fps": layout.panel.fps if layout else None,
            "resolution": self.registry.resolution(),
            "unavailable": self.registry.unavailable(),
            "warnings": (self._renderer.warnings() if self._renderer else []) + self.store.errors,
            "frames": self.stats["frames"],
            "last_error": self._last_error,
        }

    def run(self):
        attempt = 0
        while not self._done():
            try:
                self._connect()
                attempt = 0
                self._serve()
            except (OSError, PanelNotFound) as e:
                self._last_error = str(e)
                self._drop_link()
                if self._done():
                    break
                self.stats["reconnects"] += 1
                delay = self.cfg.reconnect_backoff[
                    min(attempt, len(self.cfg.reconnect_backoff) - 1)]
                self._clock.sleep(delay)
                attempt += 1

    def _done(self):
        if self._stop:
            return True
        limit = self.cfg.max_iterations
        return limit is not None and self.stats["frames"] >= limit

    # --- conexion ---

    def _connect(self):
        if self._link is not None:
            return
        link = self._link_factory()
        link.open()
        layout = self.store.current
        if layout is None:
            raise OSError("no hay un layout valido cargado")
        link.set_brightness(layout.panel.brightness)
        self._link = link
        self._renderer = Renderer(layout, panel_size=link.geometry)

    def _drop_link(self):
        if self._link is not None:
            try:
                self._link.close()
            except Exception:
                pass
        self._link = None
        self._renderer = None

    # --- loop ---

    def _serve(self):
        while not self._done():
            t0 = self._clock.time()
            self._render_once()
            period = 1.0 / max(0.1, self.store.current.panel.fps)
            if self._done():
                return
            self._clock.sleep(max(0.0, period - (self._clock.time() - t0)))

    def _render_once(self):
        self._refresh_layout()
        self._refresh_sample()
        layout = self.store.current
        img = self._renderer.frame(self._sample, self._history.series())
        self._link.send_frame(to_jpeg(img, layout.panel.rotate, layout.panel.jpeg_quality))
        self.stats["frames"] += 1

    def _refresh_layout(self):
        changed, _errors = self.store.reload_if_changed()
        if changed and self._renderer is not None:
            layout = self.store.current
            self._renderer.set_layout(layout)
            self._link.set_brightness(layout.panel.brightness)

    def _refresh_sample(self):
        now = self._clock.time()
        if self._sample and now - self._last_sample_at < self.cfg.sample_period:
            return
        self._sample = self.registry.read()
        self._history.push(self._sample)
        self._last_sample_at = now
```

Ojo con `_refresh_layout`: llama `set_brightness` en cada recarga porque el brillo vive en el perfil. Es un write de 5 bytes, no un frame, así que no cuesta nada.

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add vmaxpanel/engine.py tests/test_engine.py
git commit -m "feat: engine con cadencias separadas, hot-reload y reconexion con backoff"
```

---

### Task 12: Perfil Vitals, humanización de tasas, golden test y CLI

Cierra la fase: el perfil por defecto reproduce lo que el panel muestra hoy, pero manejado por datos, con las etiquetas como widgets y **fondo original** en lugar de `back.png`.

Falta una pieza para lograr paridad: hoy `human_rate()` convierte 1258291 en `"1.2 MB/s"`, y una plantilla de `str.format` no puede hacer eso. Se agrega un campo `humanize` al widget `text`, con tres valores: `"none"` (default), `"rate"` (B/s → KB/s → MB/s) y `"bytes"`.

**Files:**
- Modify: `vmaxpanel/layout/model.py` (campo `humanize` en `TextWidget`)
- Modify: `vmaxpanel/layout/schema.py` (validar `humanize`)
- Modify: `vmaxpanel/render/widgets.py` (aplicarlo en `format_value`)
- Create: `vmaxpanel/profiles/vitals.json`, `vmaxpanel/cli.py`
- Test: `tests/test_vitals_profile.py`, `tests/golden/vitals.png`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: `widgets.HUMANIZERS: dict[str, callable]`, `widgets.human_rate(bps) -> str`; `cli.main(argv=None) -> int`; `cli.default_profile_path() -> Path`.

- [ ] **Step 1: Escribir los tests de humanización que fallan**

Agregar a `tests/test_widgets.py`:

```python
def test_human_rate_scales_the_unit():
    assert widgets.human_rate(500) == "500 B/s"
    assert widgets.human_rate(2048) == "2 KB/s"
    assert widgets.human_rate(5 * 1048576) == "5.0 MB/s"


def test_humanize_rate_replaces_the_format():
    w = text_widget(metric="net.down", format="{}", humanize="rate")
    assert widgets.format_value(w, 1258291) == "1.2 MB/s"


def test_humanize_dashes_on_missing_value():
    w = text_widget(metric="net.down", format="{}", humanize="rate")
    assert widgets.format_value(w, UNAVAILABLE) == widgets.DASH


def test_humanize_bytes_uses_binary_units():
    w = text_widget(metric="mem.used", format="{}", humanize="bytes")
    assert widgets.format_value(w, 3221225472) == "3.0 GiB"


def test_unknown_humanizer_falls_back_to_format():
    w = text_widget(format="{:.0f}", humanize="inventado")
    assert widgets.format_value(w, 7.0) == "7"
```

Agregar a `tests/test_schema.py`:

```python
def test_humanize_must_be_a_known_mode():
    def check(mode):
        return schema.validate(with_widget(
            {"id": "w", "type": "text", "metric": "net.down", "x": 0, "y": 0,
             "font": "mono-14", "color": "#FFFFFF", "format": "{}",
             "humanize": mode}))

    assert check("rate") == []
    assert check("bytes") == []
    assert check("none") == []
    assert any("humanize" in e for e in check("plasma"))
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_widgets.py tests/test_schema.py -v`
Expected: FAIL — `TypeError: TextWidget.__init__() got an unexpected keyword argument 'humanize'`

- [ ] **Step 3: Implementar la humanización**

En `vmaxpanel/layout/model.py`, agregar el campo a `TextWidget`:

```python
@dataclass
class TextWidget(Widget):
    metric: str = ""
    font: str = ""
    color: str = "#FFFFFF"
    format: str = "{}"
    align: str = "left"
    humanize: str = "none"
    rules: list[Rule] = field(default_factory=list)
```

En `vmaxpanel/layout/schema.py`, agregar la constante y la validación:

```python
HUMANIZE_MODES = {"none", "rate", "bytes"}
```

y dentro de `_validate_widget`, en la rama `if t == "text":`, antes del chequeo de `format`:

```python
    if t == "text":
        if w.get("humanize", "none") not in HUMANIZE_MODES:
            errs.append(f"{where}: humanize {w.get('humanize')!r} invalido, "
                        f"se espera uno de {sorted(HUMANIZE_MODES)}")
        if "format" in w:
```

En `vmaxpanel/render/widgets.py`, agregar los humanizadores y usarlos en `format_value`:

```python
def human_rate(bps) -> str:
    if bps >= 1048576:
        return f"{bps / 1048576:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def human_bytes(b) -> str:
    for unit, div in (("GiB", 1073741824), ("MiB", 1048576), ("KiB", 1024)):
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return f"{b:.0f} B"


HUMANIZERS = {"rate": human_rate, "bytes": human_bytes}
```

y reemplazar `format_value` por:

```python
def format_value(w: model.TextWidget, value) -> str:
    """Aplica humanize si corresponde, si no w.format.

    Un valor ausente deja "--" conservando el sufijo del template.
    """
    humanizer = HUMANIZERS.get(getattr(w, "humanize", "none"))
    if humanizer is not None:
        v = _num(value)
        return DASH if v is None else humanizer(v)
    if value is None or value is UNAVAILABLE:
        return _dashed(w.format)
    try:
        return w.format.format(value)
    except (ValueError, TypeError):
        return _dashed(w.format)
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `python -m pytest tests/test_widgets.py tests/test_schema.py -v`
Expected: PASS, 25 + 19 tests

- [ ] **Step 5: Escribir el perfil Vitals**

Las coordenadas de los valores son las mismas que hoy tiene `Renderer.frame()` en `daemon/panel.py`, así que la paridad es exacta ahí. Las **etiquetas** son nuevas: estaban horneadas en `back.png` y sus posiciones se estiman acá y se ajustan mirando el preview en el paso 7.

`vmaxpanel/profiles/vitals.json`:

```json
{
  "version": 1,
  "name": "Vitals",
  "designed_for": { "width": 320, "height": 1480 },
  "panel": { "rotate": 180, "brightness": 100, "fps": 1, "jpeg_quality": 82 },
  "fonts": {
    "hero": { "family": "Consolas", "size": 74, "bold": true },
    "big": { "family": "Consolas", "size": 60 },
    "value": { "family": "Consolas", "size": 28 },
    "small": { "family": "Consolas", "size": 26 },
    "caption": { "family": "Consolas", "size": 20 },
    "tag": { "family": "Consolas", "size": 14 }
  },
  "background": {
    "type": "gradient",
    "angle": 90,
    "stops": [
      { "at": 0.0, "color": "#101725" },
      { "at": 0.45, "color": "#0B0F17" },
      { "at": 1.0, "color": "#141A26" }
    ]
  },
  "widgets": [
    { "id": "clock", "type": "text", "metric": "clock.time", "x": 18, "y": 20,
      "font": "hero", "color": "#FFFFFF", "format": "{}" },
    { "id": "date", "type": "text", "metric": "clock.date", "x": 24, "y": 104,
      "font": "caption", "color": "#898781", "format": "{}" },

    { "id": "cpu-hdr", "type": "label", "text": "CPU", "x": 24, "y": 200,
      "font": "tag", "color": "#3987E5" },
    { "id": "cpu-name", "type": "text", "metric": "cpu.name", "x": 24, "y": 230,
      "font": "tag", "color": "#898781", "format": "{}" },
    { "id": "cpu-load", "type": "text", "metric": "cpu.load", "x": 20, "y": 248,
      "font": "big", "color": "#FFFFFF", "format": "{:.1f}%",
      "rules": [ { "when": "> 90", "color": "#FF5555" } ] },
    { "id": "cpu-bar", "type": "bar", "metric": "cpu.load", "x": 24, "y": 316,
      "w": 272, "h": 16, "radius": 5, "fill": "#3987E5", "track": "#242834" },
    { "id": "cpu-temp-tag", "type": "label", "text": "TEMP", "x": 22, "y": 362,
      "font": "tag", "color": "#898781" },
    { "id": "cpu-temp", "type": "text", "metric": "cpu.temp", "x": 22, "y": 382,
      "font": "value", "color": "#86B6EF", "format": "{:.0f}°",
      "rules": [ { "when": "> 85", "color": "#FF5555" } ] },
    { "id": "cpu-clock-tag", "type": "label", "text": "CLOCK", "x": 158, "y": 362,
      "font": "tag", "color": "#898781" },
    { "id": "cpu-clock", "type": "text", "metric": "cpu.clock", "x": 158, "y": 382,
      "font": "value", "color": "#86B6EF", "format": "{:.0f}" },
    { "id": "vcore-tag", "type": "label", "text": "VCORE", "x": 22, "y": 434,
      "font": "tag", "color": "#898781" },
    { "id": "vcore", "type": "text", "metric": "cpu.vcore", "x": 22, "y": 454,
      "font": "value", "color": "#86B6EF", "format": "{:.2f}V" },
    { "id": "vrm-tag", "type": "label", "text": "VRM", "x": 158, "y": 434,
      "font": "tag", "color": "#898781" },
    { "id": "vrm", "type": "text", "metric": "cpu.vrm_temp", "x": 158, "y": 454,
      "font": "value", "color": "#86B6EF", "format": "{:.0f}°" },

    { "id": "gpu-hdr", "type": "label", "text": "GPU", "x": 24, "y": 544,
      "font": "tag", "color": "#3987E5" },
    { "id": "gpu-name", "type": "text", "metric": "gpu.name", "x": 24, "y": 574,
      "font": "tag", "color": "#898781", "format": "{}" },
    { "id": "gpu-load", "type": "text", "metric": "gpu.load", "x": 20, "y": 592,
      "font": "big", "color": "#FFFFFF", "format": "{:.0f}%" },
    { "id": "gpu-bar", "type": "bar", "metric": "gpu.load", "x": 24, "y": 660,
      "w": 272, "h": 16, "radius": 5, "fill": "#3987E5", "track": "#242834" },
    { "id": "gpu-temp-tag", "type": "label", "text": "TEMP / HOT", "x": 22, "y": 706,
      "font": "tag", "color": "#898781" },
    { "id": "gpu-temp", "type": "text", "metric": "gpu.temp", "x": 22, "y": 726,
      "font": "value", "color": "#86B6EF", "format": "{:.0f}/" },
    { "id": "gpu-hotspot", "type": "text", "metric": "gpu.hotspot", "x": 78, "y": 726,
      "font": "value", "color": "#86B6EF", "format": "{:.0f}°" },
    { "id": "gpu-clock-tag", "type": "label", "text": "CLOCK", "x": 158, "y": 706,
      "font": "tag", "color": "#898781" },
    { "id": "gpu-clock", "type": "text", "metric": "gpu.clock", "x": 158, "y": 726,
      "font": "value", "color": "#86B6EF", "format": "{:.0f}" },
    { "id": "gpu-power-tag", "type": "label", "text": "POWER", "x": 22, "y": 778,
      "font": "tag", "color": "#898781" },
    { "id": "gpu-power", "type": "text", "metric": "gpu.power", "x": 22, "y": 798,
      "font": "value", "color": "#86B6EF", "format": "{:.0f}W" },
    { "id": "gpu-vram-tag", "type": "label", "text": "VRAM", "x": 158, "y": 778,
      "font": "tag", "color": "#898781" },
    { "id": "gpu-vram", "type": "text", "metric": "gpu.vram", "x": 158, "y": 798,
      "font": "value", "color": "#86B6EF", "format": "{:.0f}%" },

    { "id": "ram-hdr", "type": "label", "text": "RAM", "x": 24, "y": 892,
      "font": "tag", "color": "#3987E5" },
    { "id": "mem-load", "type": "text", "metric": "mem.load", "x": 20, "y": 922,
      "font": "big", "color": "#FFFFFF", "format": "{:.1f}%" },
    { "id": "mem-bar", "type": "bar", "metric": "mem.load", "x": 24, "y": 990,
      "w": 272, "h": 16, "radius": 5, "fill": "#3987E5", "track": "#242834" },
    { "id": "mem-used-tag", "type": "label", "text": "USED", "x": 22, "y": 1036,
      "font": "tag", "color": "#898781" },
    { "id": "mem-used", "type": "text", "metric": "mem.used", "x": 22, "y": 1056,
      "font": "value", "color": "#86B6EF", "format": "{:.1f}G" },
    { "id": "mem-speed-tag", "type": "label", "text": "SPEED", "x": 158, "y": 1036,
      "font": "tag", "color": "#898781" },
    { "id": "mem-speed", "type": "label", "text": "6000", "x": 158, "y": 1056,
      "font": "value", "color": "#86B6EF" },

    { "id": "sys-hdr", "type": "label", "text": "SYS", "x": 24, "y": 1180,
      "font": "tag", "color": "#3987E5" },
    { "id": "down-tag", "type": "label", "text": "DOWN", "x": 22, "y": 1210,
      "font": "tag", "color": "#898781" },
    { "id": "down", "type": "text", "metric": "net.down", "x": 22, "y": 1230,
      "font": "small", "color": "#86B6EF", "format": "{}", "humanize": "rate" },
    { "id": "up-tag", "type": "label", "text": "UP", "x": 22, "y": 1280,
      "font": "tag", "color": "#898781" },
    { "id": "up", "type": "text", "metric": "net.up", "x": 22, "y": 1300,
      "font": "small", "color": "#86B6EF", "format": "{}", "humanize": "rate" },
    { "id": "ssd-tag", "type": "label", "text": "SSD", "x": 22, "y": 1350,
      "font": "tag", "color": "#898781" },
    { "id": "ssd-0", "type": "text", "metric": "disk.temp.0", "x": 22, "y": 1370,
      "font": "small", "color": "#86B6EF", "format": "{:.0f}°" },
    { "id": "ssd-1", "type": "text", "metric": "disk.temp.1", "x": 92, "y": 1370,
      "font": "small", "color": "#86B6EF", "format": "{:.0f}°" },
    { "id": "ssd-2", "type": "text", "metric": "disk.temp.2", "x": 162, "y": 1370,
      "font": "small", "color": "#86B6EF", "format": "{:.0f}°" }
  ]
}
```

Dos desvíos deliberados respecto de hoy, ambos consecuencia de pasar a datos:

1. `gpu_temps` era un string armado en `collect()` (`"51/68°"`). Ahora son dos widgets, `gpu-temp` con formato `{:.0f}/` y `gpu-hotspot` al lado. Si la GPU no reporta hot spot, se ve `51/--°` en vez de `51°`.
2. `mem_speed` era el literal `'6000'`. Ahora es un widget `label` con ese texto — editable desde la GUI, y correcto para cualquiera que tenga otra RAM.

- [ ] **Step 6: Escribir el test del perfil y del golden**

`tests/test_vitals_profile.py`:

```python
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from vmaxpanel.layout import loader, schema
from vmaxpanel.metrics import UNAVAILABLE, is_metric
from vmaxpanel.render.renderer import Renderer, to_jpeg

PROFILE = Path("vmaxpanel/profiles/vitals.json")
GOLDEN = Path("tests/golden/vitals.png")

SAMPLE = {
    "clock.time": "14:32", "clock.date": "LUN 11 AGO",
    "cpu.name": "INTEL CORE i5-12400F", "cpu.load": 55.5, "cpu.temp": 48.0,
    "cpu.clock": 4080.0, "cpu.vcore": 1.05, "cpu.vrm_temp": 41.0,
    "cpu.power": UNAVAILABLE, "cpu.fan": UNAVAILABLE,
    "gpu.name": "AMD RADEON RX 6800 XT", "gpu.load": 23.0, "gpu.temp": 51.0,
    "gpu.hotspot": 68.0, "gpu.clock": 1850.0, "gpu.power": 84.0, "gpu.vram": 37.0,
    "mem.load": 42.3, "mem.used": 13.5, "mem.total": 32.0,
    "net.down": 1258291.0, "net.up": 40960.0,
    "disk.temp.0": 34.0, "disk.temp.1": 40.0, "disk.temp.2": 41.0,
}


def test_profile_is_valid():
    raw = __import__("json").loads(PROFILE.read_text(encoding="utf-8"))
    assert schema.validate(raw) == []


def test_profile_only_references_known_metrics():
    lay = loader.load(PROFILE)
    for w in lay.widgets:
        mid = getattr(w, "metric", None)
        if mid:
            assert is_metric(mid), mid


def test_profile_ships_no_bundled_font_files():
    """Consolas es de Microsoft: el perfil la pide por familia, no por archivo."""
    lay = loader.load(PROFILE)
    for f in lay.fonts.values():
        assert not f.family.lower().endswith((".ttf", ".otf"))


def test_profile_uses_no_vendor_artwork():
    """back.png es arte del tema Vitals de LCD Control y no se redistribuye."""
    lay = loader.load(PROFILE)
    assert lay.background.type == "gradient"
    assert lay.background.src is None


def test_frame_matches_the_golden_image():
    im = Renderer(loader.load(PROFILE)).frame(SAMPLE)
    if not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        im.save(GOLDEN)
        pytest.skip("golden generado; revisalo a ojo y volve a correr")
    diff = ImageChops.difference(im, Image.open(GOLDEN).convert("RGB"))
    bbox = diff.getbbox()
    if bbox is None:
        return
    worst = max(max(band.getextrema()) for band in diff.split())
    assert worst <= 8, f"el render cambio respecto del golden (delta {worst})"


def test_unavailable_metrics_render_as_dashes_not_crashes():
    sample = dict(SAMPLE, cpu_temp=None)
    sample["cpu.temp"] = UNAVAILABLE
    sample["gpu.hotspot"] = UNAVAILABLE
    im = Renderer(loader.load(PROFILE)).frame(sample)
    assert im.size == (320, 1480)


def test_end_to_end_frame_fits_the_panel_protocol():
    lay = loader.load(PROFILE)
    data = to_jpeg(Renderer(lay).frame(SAMPLE), lay.panel.rotate, lay.panel.jpeg_quality)
    assert data[:3] == b"\xff\xd8\xff" and data[-2:] == b"\xff\xd9"
    assert len(data) < 200_000          # entra holgado en un write serial
```

- [ ] **Step 7: Generar el golden y revisarlo a ojo**

Run: `python -m pytest tests/test_vitals_profile.py -v`
Expected: 6 PASS y 1 SKIP (`golden generado`), con `tests/golden/vitals.png` creado.

Abrir `tests/golden/vitals.png` y compararlo con `research/preview_full2.png`, que es el layout actual. Las etiquetas nuevas (`TEMP`, `CLOCK`, `VCORE`, `VRM`, `POWER`, `VRAM`, `USED`, `SPEED`, `DOWN`, `UP`, `SSD`) y los encabezados de sección estaban horneados en `back.png`, así que sus posiciones son estimadas: ajustar `x`/`y` en `vitals.json`, borrar el golden y repetir hasta que quede parecido.

Run: `python -m pytest tests/test_vitals_profile.py -v`
Expected: PASS, 7 tests

- [ ] **Step 8: Implementar el CLI**

`vmaxpanel/cli.py`:

```python
"""Punto de entrada de fase 1: python -m vmaxpanel

En la fase 3 esto queda debajo del servicio; por ahora reemplaza a
daemon/panel.py sin dejar de arrancarse a mano.
"""
import argparse
import sys
import time
from pathlib import Path

from .engine import Engine, EngineConfig
from .layout import loader
from .providers.msr import MsrProvider
from .providers.psutil_provider import PsutilProvider
from .providers.registry import Registry
from .providers.sidecar import SidecarClient
from .providers.sidecar_providers import Gsa1Provider, LhmProvider, PdhProvider
from .render.renderer import Renderer
from .transport.panel_link import PanelLink

HERE = Path(__file__).resolve().parent
SIDECAR = HERE / "sensors.ps1"


def default_profile_path() -> Path:
    return HERE / "profiles" / "vitals.json"


def build_registry(sidecar_script=SIDECAR, warmup=25.0):
    client = SidecarClient(sidecar_script).start()
    if not client.wait_ready(warmup):
        print("aviso: el sidecar no entrego datos; los sensores de hardware "
              "van a quedar no disponibles", file=sys.stderr)
    return Registry([PsutilProvider(), Gsa1Provider(client), PdhProvider(client),
                     LhmProvider(client), MsrProvider()]), client


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vmaxpanel")
    ap.add_argument("--profile", type=Path, default=default_profile_path())
    ap.add_argument("--save", type=Path, help="renderiza un PNG y sale, sin tocar el panel")
    ap.add_argument("--port", help="COM del panel; por defecto se autodetecta")
    ap.add_argument("--once", action="store_true", help="manda un solo frame")
    ap.add_argument("--no-sensors", action="store_true",
                    help="no lanza el sidecar (util para probar layouts)")
    a = ap.parse_args(argv)

    store = loader.ProfileStore(a.profile)
    errors = store.load_now()
    if errors:
        for e in errors:
            print(f"layout: {e}", file=sys.stderr)
        return 2

    if a.no_sensors:
        registry, client = Registry([PsutilProvider()]), None
    else:
        registry, client = build_registry()

    try:
        if a.save:
            r = Renderer(store.current)
            r.frame(registry.read()).save(a.save)
            for w in r.warnings():
                print(f"aviso: {w}", file=sys.stderr)
            print("guardado", a.save)
            return 0

        cfg = EngineConfig(profile_path=a.profile, max_iterations=1 if a.once else None)
        eng = Engine(store, registry, cfg,
                     link_factory=lambda: PanelLink.autodetect(a.port))
        print(f"perfil {store.current.name!r}; "
              f"metricas no disponibles: {sorted(registry.unavailable())}")
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
```

Y `vmaxpanel/__main__.py`:

```python
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 9: Probar el CLI sin tocar el panel**

Run: `python -m vmaxpanel --save preview.png --no-sensors`
Expected: escribe `preview.png`; las métricas de hardware salen `--` porque el sidecar no corrió.

Run: `python -m vmaxpanel --save preview_full.png`
Expected: tarda unos segundos (arranca el sidecar) y sale con datos reales de CPU, GPU y discos. Imprime avisos si falta alguna fuente.

- [ ] **Step 10: Probar contra el panel real**

El daemon viejo tiene el puerto: liberarlo primero.

Run: `daemon\stop.ps1`
Run: `python -m vmaxpanel --once`
Expected: el panel muestra un frame del layout nuevo.

Run: `python -m vmaxpanel`
Expected: el panel se actualiza a 1 fps. Editar `vmaxpanel/profiles/vitals.json` (mover un widget) y guardar: el cambio aparece en el panel **sin reiniciar**. Cortar con Ctrl+C.

- [ ] **Step 11: Correr toda la suite**

Run: `python -m pytest`
Expected: PASS, ~121 tests

- [ ] **Step 12: Commit**

```bash
git add vmaxpanel/profiles/vitals.json vmaxpanel/cli.py vmaxpanel/__main__.py vmaxpanel/layout/model.py vmaxpanel/layout/schema.py vmaxpanel/render/widgets.py tests/test_vitals_profile.py tests/golden/vitals.png tests/test_widgets.py tests/test_schema.py
git commit -m "feat: perfil Vitals data-driven con fondo propio, humanizacion de tasas y CLI"
```

- [ ] **Step 13: Actualizar la documentación**

En `README.md`, reemplazar la sección **Editar** por la tabla nueva:

| Qué querés cambiar | Dónde |
|---|---|
| Posición, formato o color de un valor | `vmaxpanel/profiles/vitals.json` — se recarga en caliente al guardar |
| Etiquetas de texto | widgets de tipo `label` en el mismo JSON |
| Fondo | el bloque `background` del perfil |
| Qué métricas existen | `vmaxpanel/metrics.py` |
| De dónde sale cada métrica | `vmaxpanel/providers/` |
| Sensores nuevos del sidecar | `vmaxpanel/sensors.ps1` |

Y agregar en `CLAUDE.md`, bajo *Lo que no hay que reinvestigar*:

- `consola.ttf`/`consolab.ttf` son **Consolas, de Microsoft**: no se redistribuyen. Las fuentes se piden por familia; en cualquier Windows están.
- `daemon/assets/back.png` es arte del tema Vitals de **LCD Control**: no se redistribuye. El fondo del perfil propio es un `gradient`.

```bash
git add README.md CLAUDE.md
git commit -m "docs: guia de edicion del motor data-driven y notas de licencia"
```

---

## Definición de terminado (fase 1)

- [ ] `python -m pytest` verde, sin tests salteados.
- [ ] `python -m vmaxpanel` maneja el panel con el perfil `vitals.json`, con paridad visual razonable respecto del layout de hoy.
- [ ] Editar el JSON y guardarlo se refleja en el panel sin reiniciar.
- [ ] Un JSON roto **no** apaga el panel: sigue el layout anterior y el error queda en `state()["warnings"]`.
- [ ] `daemon/panel.py` sigue funcionando, sin tocar: es la vuelta atrás si algo sale mal.
- [ ] Ningún archivo del repo contiene `COM3`, `320, 1480`, `2500`, `i5-12400F` ni `RX 6800 XT` como valor hardcodeado fuera de un fallback documentado.
- [ ] No se empaqueta ningún TTF ni `back.png`.

## Self-review del plan

**Cobertura del spec.** Fase 1 del spec cubierta: providers con degradado (Tasks 2, 3), ids canónicos (Task 1), `layout.json` con schema (Task 4), hot-reload con mantener-el-anterior (Task 5), renderer de widgets (Tasks 6-9), etiquetas como widgets y fondo original (Task 12), golden tests (Task 12), autodetección de puerto y geometría (Task 10). Los desvíos respecto del spec, todos anotados en su tarea: se agregó `humanize` (no estaba en el spec, hace falta para paridad con `human_rate()`), y `sequence`/`video`/`procedural` degradan a `solid` con aviso en vez de fallar.

**Fuera de esta fase, por diseño:** IPC y su DACL, servicio de Windows, tray, editor, fondos animados y el spike de throughput. Van en los planes de fases 2 y 3.

**Consistencia de tipos.** `Size` se usa igual en `model`, `renderer`, `panel_link` y `background`. `DrawCtx` se construye solo en `renderer.frame()` y en los tests de widgets. `Provider.metrics()` devuelve `set[str]` en los cuatro providers. `LhmProvider.served` es una property y `metrics()` la envuelve — el registry llama `metrics()`, nunca `served`.

**Riesgo conocido:** `LhmProvider.metrics()` es dinámico, así que el `Registry` tiene que construirse **después** de la primera muestra del sidecar. `cli.build_registry()` lo garantiza con `wait_ready()`. Si alguien arma el `Registry` antes, los `disk.temp.N` quedan afuera y no vuelven. Está anotado en Task 3, Step 6.

