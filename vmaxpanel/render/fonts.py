"""Resuelve alias de fuente a archivos reales, por nombre de familia.

No empaquetamos TTFs: consola.ttf/consolab.ttf son Consolas, de Microsoft, y no
son redistribuibles. Se busca por familia en assets/fonts/ (donde la fase 3
pondra una mono libre) y despues entre las fuentes del sistema.

Una familia ausente cae al fallback y se anota en missing(), para que el editor
lo pueda avisar. Nunca lanza: un layout ajeno no puede tumbar el render.

Limitacion conocida: un .ttc puede empaquetar varias caras (ej. regular +
bold) en un solo archivo, pero solo leemos la cara 0 para nombrar el archivo.
En las fuentes de Windows observadas, las variantes de peso vienen en .ttc
separados (msjh.ttc / msjhbd.ttc), no como caras extra de un mismo archivo,
asi que esto no perdio ninguna variante en la practica. Si algun dia aparece
un .ttc que si empaqueta pesos como caras adicionales, esa cara bold no se
va a indexar; no vale la pena la complejidad de escanear todas las caras de
todos los archivos para un caso que no se dio.
"""
import os
from pathlib import Path

from PIL import ImageFont

BUNDLED = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_EXTS = (".ttf", ".otf", ".ttc")
_BOLD_HINTS = ("bold", "bd", "black", "heavy", "semibold")
_EXPLICIT_NONBOLD_STYLES = ("regular", "normal", "book", "roman", "light", "medium")


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
    """Busca fuentes por familia y cachea el resultado. No requiere estar
    en Windows: fuera de Windows simplemente no hay directorios de sistema
    y todo cae al fallback empaquetado o al default de PIL.
    """

    def __init__(self, extra_dirs: list[Path] | None = None):
        # orden de precedencia: extra_dirs > empaquetadas > sistema
        self._dirs = [Path(d) for d in (extra_dirs or [])] + [BUNDLED] + _system_font_dirs()
        self._index: dict[str, dict[str, Path]] | None = None
        self._cache: dict[tuple, ImageFont.FreeTypeFont] = {}
        self._missing: set[str] = set()

    def index(self) -> dict[str, dict[str, Path]]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def _build_index(self) -> dict[str, dict[str, Path]]:
        idx: dict[str, dict[str, Path]] = {}
        for d in self._dirs:
            if not d.is_dir():
                # extra_dirs lo puede pasar el caller con cualquier cosa;
                # un directorio inexistente no debe tumbar el indexado.
                continue
            for path in sorted(d.iterdir()):
                if path.suffix.lower() not in _EXTS or not path.is_file():
                    continue
                family, style = self._names(path)
                if not family:
                    continue
                slot = "bold" if self._is_bold(style, path.stem) else "regular"
                # setdefault: el primer directorio en _dirs que trae una
                # familia/slot gana. Como extra_dirs va primero en la lista,
                # una copia ahi pisa a la del sistema para la misma familia.
                idx.setdefault(family.lower(), {}).setdefault(slot, path)
        return idx

    @staticmethod
    def _names(path: Path) -> tuple[str | None, str]:
        try:
            f = ImageFont.truetype(os.fspath(path), 12)
            family, style = f.getname()
            return family or path.stem, style or ""
        except Exception:
            # un archivo corrupto o ilegible no debe tumbar el indexado
            # completo; se lo salta y se sigue con el resto.
            return None, ""

    @staticmethod
    def _is_bold(style: str, stem: str) -> bool:
        style_l = style.lower().strip()
        # El style que reporta la propia fuente es mas confiable que el
        # nombre de archivo. Si dice explicitamente que es la variante
        # regular (ej. Webdings, o "Eras Bold ITC" cuyo *estilo* es
        # "Regular" aunque el *nombre de familia* diga "Bold"), no lo
        # pisamos por una coincidencia de substring en el archivo (ej.
        # "ERASBD.TTF", "webdings.ttf" contienen "bd" por casualidad).
        if style_l in _EXPLICIT_NONBOLD_STYLES:
            return False
        if any(h in style_l for h in _BOLD_HINTS):
            return True
        return any(h in stem.lower() for h in _BOLD_HINTS)

    def missing(self) -> set[str]:
        return set(self._missing)

    def resolve(self, font, scale: float = 1.0) -> ImageFont.FreeTypeFont:
        size = max(1, int(round(font.size * scale)))
        key = (font.family.lower(), size, font.bold)
        if key in self._cache:
            return self._cache[key]

        try:
            path = self._pick_path(font)
        except Exception:
            # un extra_dir roto, un permiso denegado, lo que sea: nunca
            # tumba el render, se anota como perdida y se cae al default.
            self._missing.add(font.family)
            path = None

        try:
            if path is not None:
                resolved = ImageFont.truetype(os.fspath(path), size)
            else:
                resolved = ImageFont.load_default(size)
        except Exception:
            resolved = ImageFont.load_default(size)

        self._cache[key] = resolved
        return resolved

    def _pick_path(self, font) -> Path | None:
        entry = self.index().get(font.family.lower())
        if entry:
            return entry.get("bold" if font.bold else "regular") or next(iter(entry.values()))
        self._missing.add(font.family)
        return self._first_bundled() or self._any_system_mono()

    def _first_bundled(self) -> Path | None:
        if not BUNDLED.is_dir():
            return None
        for p in sorted(BUNDLED.iterdir()):
            if p.suffix.lower() in _EXTS and p.is_file():
                return p
        return None

    def _any_system_mono(self) -> Path | None:
        for family in ("consolas", "cascadia mono", "courier new", "dejavu sans mono"):
            entry = self.index().get(family)
            if entry:
                return entry.get("regular") or next(iter(entry.values()))
        return None
