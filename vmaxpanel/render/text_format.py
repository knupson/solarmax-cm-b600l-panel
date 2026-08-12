"""Formateo del valor de un widget de texto: template, humanize y color.

Separado de widgets.py porque no toca PIL ni fuentes: es puro texto y reglas de
color, la mitad de ese modulo que se puede leer y probar sin nada de dibujo.
widgets.py lo reexporta, asi que quien ya importaba `widgets.format_value` sigue
andando.
"""
import math

from ..layout import model
from ..metrics import UNAVAILABLE

DASH = "--"


def _num(value):
    """Numero utilizable para una fraccion de barra/arco/grafico o para
    evaluar una regla de color. Rechaza bool (isinstance(True, int) es
    True en Python) y tambien NaN/Inf: un sensor fallado que devuelve nan
    no puede tratarse como "un numero cualquiera", porque
    max(0.0, min(1.0, nan)) da 1.0 por como Python compara con NaN, y una
    lectura basura terminaria dibujandose como si fuera un 100% real.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def human_rate(bps) -> str:
    """1258291 -> "1.2 MB/s": paridad con el human_rate() de daemon/panel.py,
    que un template de str.format no puede reproducir (no hay forma de
    elegir la unidad segun la magnitud dentro de un unico campo)."""
    if bps >= 1048576:
        return f"{bps / 1048576:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def human_bytes(b) -> str:
    """3221225472 -> "3.0 GiB": mismo problema que human_rate() pero en
    unidades binarias de almacenamiento en vez de una tasa por segundo."""
    for unit, div in (("GiB", 1073741824), ("MiB", 1048576), ("KiB", 1024)):
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return f"{b:.0f} B"


def human_duration(segundos) -> str:
    """33098 -> "9h 11m": una duracion como la diria una persona.

    Dos unidades como maximo y siempre las dos mas significativas: "1d 1h" en
    vez de "1d 1h 0m 0s". Un uptime en segundos crudos no le dice nada a nadie
    en un panel que se mira de reojo.
    """
    s = int(max(0, segundos))
    d, resto = divmod(s, 86400)
    h, resto = divmod(resto, 3600)
    m, seg = divmod(resto, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {seg}s"
    return f"{seg}s"


HUMANIZERS = {"rate": human_rate, "bytes": human_bytes,
              "duration": human_duration}


def format_value(w: model.TextWidget, value) -> str:
    """Aplica humanize si corresponde, si no w.format.

    Un valor ausente deja "--" (ver _dashed): con humanize activo no hay
    template del que conservar un sufijo, asi que se devuelve DASH a secas.
    Un modo de humanize desconocido (una defensa extra: schema.validate()
    ya lo rechaza, pero format_value no vuelve a validar) cae de vuelta al
    formato normal en vez de fallar.
    """
    humanizer = HUMANIZERS.get(getattr(w, "humanize", "none"))
    if humanizer is not None:
        v = _num(value)
        return DASH if v is None else humanizer(v)
    if value is None or value is UNAVAILABLE:
        return _dashed(w.format)
    try:
        return w.format.format(value)
    except Exception:
        return _dashed(w.format)


def _dashed(fmt: str) -> str:
    """"{:.0f} MHz" -> "-- MHz": reemplaza el campo por DASH sin perder el
    resto del template."""
    try:
        return fmt.format(_Dash())
    except Exception:
        return DASH


class _Dash:
    """Sustituto que se formatea a si mismo como "--" sin importar el
    format_spec (ancho, alineacion, precision, tipo de presentacion).

    __repr__ tiene que devolver DASH tambien: una conversion "{!r}" o
    "{!s}" en el template llama a repr()/str() ANTES de invocar a
    __format__ (object.__str__ ademas delega a __repr__, asi que definir
    uno solo ya cubre !r, !s y !a). Sin esto, un layout compartido con un
    format como "{!r} MHz" filtraria el repr default de Python
    ("<...widgets._Dash object at 0x...>") al panel en vez de "-- MHz".
    """

    def __format__(self, spec):
        return DASH

    def __repr__(self):
        return DASH


def color_for(w: model.TextWidget, value) -> str:
    v = _num(value)
    if v is not None:
        for rule in w.rules:
            if rule.matches(v):
                return rule.color
    return w.color
