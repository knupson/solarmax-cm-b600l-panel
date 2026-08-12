"""Carga, guardado y recarga en caliente de perfiles.

Invariante: un layout invalido NUNCA reemplaza al que esta andando. El panel no
se queda negro por un JSON mal escrito.
"""
import decimal
import hashlib
import json
import os
from dataclasses import asdict

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
    # Se serializan TODOS los campos del dataclass, no solo los que difieren
    # del default. Omitir un campo cuando su valor coincide con el default
    # rompe el roundtrip apenas ese campo es obligatorio (p.ej. "color" en un
    # TextWidget cuyo color final es el mismo #FFFFFF con el que arranca la
    # clase): validate() lo marca como "falta el campo obligatorio". Como
    # todos los campos de un dataclass de widget son claves permitidas por
    # schema.py (allowed_keys = cls.__dataclass_fields__), emitirlos todos
    # nunca puede disparar el error de "clave desconocida".
    #
    # La unica excepcion son los campos que default a None, que es como el
    # modelo dice "esto no esta puesto": un rect con fill y sin stroke
    # emitia "stroke": null, y null no es un #RRGGBB, asi que save() escribia
    # un archivo que load() rechazaba -- el editor de fase 3 guarda por aca.
    # Se omiten en vez de aflojar la validacion: un null escrito a mano sigue
    # siendo un error, porque en un campo con default no-None (bar.fill,
    # text.color) significaria "no dibujes nada" sin decirlo.
    d = {k: v for k, v in asdict(w).items()
         if not (v is None and _defaults_to_none(type(w), k))}
    if "rules" in d:
        d["rules"] = [{"when": f"{r.op} {_format_rule_value(r.value)}", "color": r.color}
                      for r in w.rules]
    return d


def _defaults_to_none(cls, field_name) -> bool:
    f = cls.__dataclass_fields__.get(field_name)
    return f is not None and f.default is None


def _format_rule_value(value: float) -> str:
    """Representa un umbral de regla en punto fijo, nunca en notacion cientifica.

    repr(value) es la representacion mas corta que hace roundtrip exacto por
    float(), pero para magnitudes muy grandes o muy chicas cae en notacion
    cientifica (p.ej. "1e+16"), que schema._RULE_RE no reconoce (":g" es
    todavia peor: cae en cientifica ya desde 1e6 y redondea a 6 cifras
    significativas, perdiendo precision). Decimal reubica el punto sin
    agregar ni perder digitos, asi que el resultado sigue siendo el mismo
    valor exacto que repr(value) ya garantizaba.
    """
    s = repr(value)
    if "e" in s or "E" in s:
        s = format(decimal.Decimal(s), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


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
        self._fp = None

    def load_now(self) -> list[str]:
        try:
            self.current = load(self.path)
            self.errors = []
        except LayoutError as e:
            self.errors = e.errors
        self._fp = self._fingerprint()
        return self.errors

    def _fingerprint(self):
        """Huella del contenido del archivo, no su mtime.

        El criterio original era `st_mtime_ns`, y dos escrituras que caen en
        el mismo tick del filesystem lo dejan igual: la segunda se perdia
        entera. Con el editor de fase 3 guardando de a dos veces seguidas eso
        deja al panel mostrando la version intermedia sin ninguna forma de
        recuperarse hasta la edicion siguiente.

        Cuesta una lectura del perfil por vuelta de polling en vez de un
        stat. Son unos pocos KB a 1-10 fps; el stat tambien era I/O, y la
        alternativa (mtime + tamano) sigue perdiendo el caso mas comun de
        edicion a mano, que es cambiar un color por otro del mismo largo.
        """
        try:
            with open(self.path, "rb") as f:
                return hashlib.sha256(f.read()).digest()
        except OSError:
            return None

    def reload_if_changed(self) -> tuple[bool, list[str]]:
        fp = self._fingerprint()
        if fp == self._fp:
            return False, []
        self._fp = fp
        try:
            new = load(self.path)
        except LayoutError as e:
            self.errors = e.errors
            return False, e.errors      # se mantiene self.current
        self.current, self.errors = new, []
        return True, []
