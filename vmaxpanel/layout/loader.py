"""Carga, guardado y recarga en caliente de perfiles.

Invariant: an invalid layout NEVER replaces the one that is running. The panel
does not go black over a badly written JSON.
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
        raise LayoutError([f"invalid JSON: {e}"]) from None
    errors = schema.validate(raw)
    if errors:
        raise LayoutError(errors)
    return schema.build(raw)


def load(path) -> Layout:
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        raise LayoutError([f"could not read {path}: {e}"]) from None
    return loads(text)


def to_dict(layout: Layout) -> dict:
    d = asdict(layout)
    d["designed_for"] = {"width": layout.designed_for.width,
                         "height": layout.designed_for.height}
    # fallbacks only when present: the vast majority of aliases do not use it, and
    # an empty key on every one clutters the file the user opens by hand.
    d["fonts"] = {a: ({"family": f.family, "size": f.size, "bold": f.bold}
                      | ({"fallbacks": list(f.fallbacks)} if f.fallbacks else {}))
                  for a, f in layout.fonts.items()}
    d["background"] = _background_dict(layout.background)
    d["widgets"] = [_widget_dict(w) for w in layout.widgets]
    return d


def _background_dict(bg) -> dict:
    """The background, with exactly the keys its type accepts.

    Derived from schema.BACKGROUND_KEYS rather than written by hand per type: an
    earlier version had an else branch emitting src/fit for anything that was
    neither solid nor gradient, so when 'procedural' appeared -- which carries
    neither -- save() started writing a background load() rejected. With derived
    keys, adding a new type cannot break the round trip.
    """
    permitidas = schema.BACKGROUND_KEYS.get(bg.type)
    if permitidas is None:
        permitidas = {"type", "color"}
    if bg.type == "gradient":
        # El validador ACEPTA color en un gradient (por tolerancia, ver
        # BACKGROUND_KEYS) but the model does not read it: emitting it adds a key to
        # the file that the user never wrote and that does nothing. Tolerance on read
        # no obliga a ensuciar al escribir.
        permitidas = permitidas - {"color"}
    out = {"type": bg.type}
    for clave in sorted(permitidas - {"type"}):
        valor = getattr(bg, clave, None)
        if valor is None:
            continue            # an undefined src cannot be written as null
        out[clave] = valor
    return out


def _widget_dict(w) -> dict:
    # EVERY field of the dataclass is serialised, not only the ones differing from
    # the default. Omitting a field when its value matches the default breaks the
    # round trip as soon as that field is required (e.g. "color" on a TextWidget
    # whose final colour is the same #FFFFFF the class starts with): validate()
    # flags it as a missing required field. Since every field of a widget dataclass
    # is a key allowed by
    # schema.py (allowed_keys = cls.__dataclass_fields__), emitirlos todos
    # it can never trigger the "unknown key" error.
    #
    # The one exception is fields defaulting to None, which is how the model says
    # "this is not set": a rect with fill and no stroke emitted "stroke": null, and
    # null is not a #RRGGBB, so save() wrote a file load() rejected -- and the
    # editor saves through here. They are omitted rather than loosening the
    # validation: a hand-written null is still an error, because on a field with a
    # non-None default (bar.fill, text.color) it would mean "draw nothing" without
    # saying so.
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
    """Renders a rule threshold in fixed point, never in scientific notation.

    repr(value) is the shortest representation that round-trips exactly through
    float(), but for very large or very small magnitudes it falls into scientific
    notation (e.g. "1e+16"), which schema._RULE_RE does not recognise (":g" is
    worse still: it goes scientific from 1e6 and rounds to 6 significant figures,
    losing precision). Decimal moves the point without adding or dropping digits,
    so the result is still the same exact value repr(value) already guaranteed.
    """
    s = repr(value)
    if "e" in s or "E" in s:
        s = format(decimal.Decimal(s), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


_serie_tmp = 0


def save(layout: Layout, path):
    save_raw(to_dict(layout), path)


def save_raw(raw: dict, path):
    """Writes a layout already in dict form, without going through the model.

    This is what the editor uses: going through `to_dict(build(raw))` rewrites the
    file in the serialiser's own order and formatting, and the profile is also
    edited by hand -- the compact two-lines-per-widget layout is part of the value.
    Saving the raw dict also cannot lose anything along the way.

    El caller es responsable de haber validado. Se escribe a un temporal y se
    then replaces: atomic, so the engine never reads a half-written file.

    The temp file carries the pid and a counter, not a fixed name: there is more
    than one possible writer -- the tray changing the fps and the editor saving --
    and with a shared `<profile>.tmp` two simultaneous writes clobber each other
    and one of them can end up writing a mixed file. If the replace fails, the temp
    file is deleted rather than left lying beside the profile.
    """
    global _serie_tmp
    _serie_tmp += 1
    tmp = f"{path}.{os.getpid()}.{_serie_tmp}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(dumps_layout(raw))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dumps_layout(raw) -> str:
    """JSON with one widget per line, the way the profile is written by hand.

    `json.dump(indent=2)` puts every key on its own line and turns a 120-line
    profile into a 535-line one: the file is still edited by hand and that
    difference matters. Each widget and each font alias is emitted compactly on one
    line; everything else gets normal indentation. It is still valid JSON -- the
    only thing that changes is where the line breaks fall.
    """
    def compacto(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))

    lineas = ["{"]
    items = list(raw.items())
    for i, (clave, valor) in enumerate(items):
        coma = "," if i < len(items) - 1 else ""
        if clave == "widgets" and isinstance(valor, list):
            lineas.append('  "widgets": [')
            for j, w in enumerate(valor):
                lineas.append(f"    {compacto(w)}"
                              f"{',' if j < len(valor) - 1 else ''}")
            lineas.append(f"  ]{coma}")
        elif clave == "fonts" and isinstance(valor, dict):
            lineas.append('  "fonts": {')
            aliases = list(valor.items())
            for j, (alias, spec) in enumerate(aliases):
                lineas.append(f"    {json.dumps(alias, ensure_ascii=False)}: "
                              f"{compacto(spec)}"
                              f"{',' if j < len(aliases) - 1 else ''}")
            lineas.append(f"  }}{coma}")
        else:
            lineas.append(f"  {json.dumps(clave, ensure_ascii=False)}: "
                          f"{compacto(valor)}{coma}")
    lineas.append("}")
    return "\n".join(lineas) + "\n"


class ProfileStore:
    """Holds the active layout and reloads it when the file changes."""

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
        """A fingerprint of the file's contents, not its mtime.

        The original rule was `st_mtime_ns`, and two writes landing in the same
        filesystem tick leave it unchanged: the second was lost entirely. With the
        editor saving twice in a row, that leaves the panel showing the
        intermediate version with no way to recover until the next edit.

        It costs one read of the profile per polling round instead of a stat. That
        is a few KB at 1-10 fps; the stat was I/O too, and the alternative (mtime +
        size) still misses the commonest hand-editing case, which is changing one
        colour for another of the same length.
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
