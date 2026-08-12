"""Resuelve alias de fuente a archivos reales, por nombre de familia.

No empaquetamos TTFs: consola.ttf/consolab.ttf son Consolas, de Microsoft, y no
son redistribuibles. Se busca por familia en assets/fonts/ (donde la fase 3
pondra una mono libre) y despues entre las fuentes del sistema.

Una familia ausente cae al fallback. **Para avisarlo se usa `is_available()`, no
`missing()`**: missing() solo anota lo que un resolve() efectivamente vio faltar,
y resolve() devuelve desde la cache en un hit sin volver a pasar por ahi, asi que
un resolver de larga vida puede quedarse mudo sobre una familia que sigue
faltando. is_available() es una consulta contra el indice, no un historial de
llamadas, y da la misma respuesta la primera vez y la enesima. Nunca lanza: un
layout ajeno no puede tumbar el render.

Un .ttc empaqueta varias caras en un archivo y **se indexan todas**. La version
anterior leia solo la cara 0, con el argumento de que en Windows los pesos vienen
en archivos separados (msjh.ttc / msjhbd.ttc). Es cierto para los pesos y falso
para las familias: msgothic.ttc trae MS Gothic, MS UI Gothic y MS PGothic como
caras 0, 1 y 2; cambria.ttc esconde Cambria Math en la 1; simsun.ttc, NSimSun. Con
la cara 0 sola, esas familias no existian para el motor -- un perfil que las
pidiera caia al fallback y el aviso decia que faltaban, estando instaladas. Y
Nirmala.ttc si empaqueta el bold como cara extra, o sea que el caso que "no se
dio" tambien estaba en esta maquina. El costo es abrir cada .ttc unas pocas veces
mas, una sola vez por proceso.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

BUNDLED = Path(__file__).resolve().parent.parent / "assets" / "fonts"
# Tope de caras por archivo. El .ttc mas gordo de Windows tiene 6 (Nirmala); 16 da
# margen de sobra y acota el escaneo por si un archivo raro devuelve caras para
# siempre en vez de levantar.
MAX_CARAS = 16
_EXTS = (".ttf", ".otf", ".ttc")
_BOLD_HINTS = ("bold", "bd", "black", "heavy", "semibold")


@dataclass(frozen=True)
class Cara:
    """Un archivo de fuente y la cara de adentro.

    `index` es casi siempre 0 -- un .ttf tiene una sola cara -- pero para un .ttc
    es la unica forma de volver a abrir la cara correcta: PIL toma el indice al
    abrir, no despues.
    """
    path: Path
    index: int = 0

    def open(self, size):
        return ImageFont.truetype(os.fspath(self.path), size, index=self.index)


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
        self._index: dict[str, dict[str, Cara]] | None = None
        self._cache: dict[tuple, ImageFont.FreeTypeFont] = {}
        self._missing: set[str] = set()
        self._substitutions: dict[str, str] = {}
        self._unreadable_dirs: set[Path] = set()

    def index(self) -> dict[str, dict[str, Cara]]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def unreadable_dirs(self) -> set[Path]:
        """Directorios de _dirs que existen pero no se pudieron listar
        (permiso denegado, recurso de red caido, etc). No tumba el
        indexado -- se saltean y el resto del indice se construye igual --
        pero es una mala configuracion real y por eso queda visible aca,
        igual que missing() deja ver las familias ausentes.
        """
        return set(self._unreadable_dirs)

    def is_available(self, family: str) -> bool:
        """True si `family` aparece en el indice de fuentes (empaquetadas o
        de sistema), sin importar el peso (bold/regular) pedido ni si
        alguna vez se llamo resolve() para ella.

        A diferencia de missing() -- que solo anota lo que un resolve()
        anterior efectivamente vio faltar, y por eso se queda calvo en un
        cache hit -- esto es una funcion pura del indice: se puede llamar
        antes de resolver nada y da la misma respuesta despues. Es lo que
        usa Renderer.warnings() para no tener que acumular ni resetear
        ningun estado propio por layout.
        """
        return bool(self.index().get(family.lower()))

    def _build_index(self) -> dict[str, dict[str, Cara]]:
        idx: dict[str, dict[str, Cara]] = {}
        for d in self._dirs:
            try:
                if not d.is_dir():
                    # extra_dirs lo puede pasar el caller con cualquier
                    # cosa; un directorio inexistente no debe tumbar el
                    # indexado.
                    continue
                paths = sorted(d.iterdir())
            except OSError:
                # permiso denegado, recurso de red caido, etc. Si esto
                # escapara de _build_index(), self._index nunca se
                # asignaria y CADA resolve() reintentaria reconstruir el
                # indice completo (todos los directorios, todos los
                # archivos) una y otra vez -- en un panel a 1 fps con
                # varios widgets de texto eso son cientos de aperturas de
                # archivo por segundo, para siempre. Se saltea el
                # directorio roto, se anota, y el resto del indice se
                # sigue construyendo y cachea normalmente.
                self._unreadable_dirs.add(d)
                continue
            for path in paths:
                if path.suffix.lower() not in _EXTS or not path.is_file():
                    continue
                for cara, family, style in self._caras(path):
                    slot = "bold" if self._is_bold(style, path.stem) else "regular"
                    # setdefault: el primer directorio en _dirs que trae una
                    # familia/slot gana. Como extra_dirs va primero en la lista,
                    # una copia ahi pisa a la del sistema para la misma familia.
                    idx.setdefault(family.lower(), {}).setdefault(slot, cara)
        return idx

    @staticmethod
    def _caras(path: Path):
        """[(Cara, familia, estilo)] de todas las caras del archivo.

        Solo los .ttc pueden traer mas de una, asi que para el resto esto abre el
        archivo una vez y corta: pedir la cara 1 de un .ttf levanta y ese except
        es el que termina el bucle.
        """
        fuera = []
        for i in range(MAX_CARAS):
            try:
                f = ImageFont.truetype(os.fspath(path), 12, index=i)
                family, style = f.getname()
            except Exception:
                # Se termino el archivo, o esta corrupto/ilegible. Ninguna de las
                # dos puede tumbar el indexado completo: se corta y se sigue con
                # el resto de los archivos, quedandose con las caras que si se
                # leyeron.
                break
            fuera.append((Cara(path, i), family or path.stem, style or ""))
        return fuera

    @staticmethod
    def _is_bold(style: str, stem: str) -> bool:
        """Si esta cara es la variante bold de su familia.

        **El estilo que declara la fuente decide; el nombre de archivo es solo el
        plan B.** Si la fuente dice cualquier estilo -- "Regular", "Italic", "Light
        Italic", "Semilight" -- se le cree: es bold solo si ese estilo lo dice. El
        nombre de archivo se mira unicamente cuando no hay estilo.

        Antes esto era una whitelist de match exacto ("regular", "light", ...), y por
        eso "Regular Italic" no matcheaba nada y caia al heuristico del nombre: ahi un
        "bd" por casualidad (ERASBD.TTF es "Eras Bold ITC" con estilo *Regular*,
        webdings.ttf tiene "bd" adentro) marcaba la cara como bold y la familia perdia
        su regular. La regla nueva no necesita enumerar los estilos que existen, que
        era el problema de fondo.
        """
        style_l = style.lower().strip()
        if style_l:
            return any(h in style_l for h in _BOLD_HINTS)
        return any(h in stem.lower() for h in _BOLD_HINTS)

    def missing(self) -> set[str]:
        """Familias que ALGUN resolve() vio faltar. Historial, no estado.

        No es el canal para avisar al usuario -- para eso esta is_available().
        resolve() devuelve desde la cache en un hit sin volver a anotar, asi que
        una familia que sigue faltando puede no aparecer aca. Se conserva porque
        sirve para diagnosticar que pidio el layout, no que hay en el sistema.
        """
        return set(self._missing)

    def resolve(self, font, scale: float = 1.0) -> ImageFont.FreeTypeFont:
        size = max(1, int(round(font.size * scale)))
        key = (font.family.lower(), size, font.bold)
        if key in self._cache:
            return self._cache[key]

        try:
            cara = self._pick_cara(font)
        except Exception:
            # un extra_dir roto, un permiso denegado, lo que sea: nunca
            # tumba el render, se anota como perdida y se cae al default.
            self._missing.add(font.family)
            cara = None

        resolved = None
        for intento in (lambda: cara.open(size) if cara is not None else None,
                        lambda: ImageFont.load_default(size),
                        # Ultimo recurso SIN size: load_default(size) de Pillow
                        # moderno abre una fuente con truetype por dentro, asi que si
                        # lo que falla es truetype -- un TTF corrupto, un disco de red
                        # caido -- el fallback falla por la MISMA razon y la excepcion
                        # se escapaba de resolve(), matando el render. La variante sin
                        # size (load_default_imagefont en Pillow 12) devuelve el
                        # bitmap incrustado y no abre ningun archivo.
                        getattr(ImageFont, "load_default_imagefont",
                                ImageFont.load_default)):
            try:
                resolved = intento()
            except Exception:
                resolved = None
            if resolved is not None:
                break

        self._cache[key] = resolved
        return resolved

    def _pick_cara(self, font) -> Cara | None:
        """La cara para este Font: su familia, o la primera de `fallbacks` que exista.

        La cadena la declara el PERFIL, no el resolver: solo el que diseno el layout
        sabe con que se parece a lo que queria. El resolver solo la recorre y anota
        con que termino dibujando, porque un reemplazo silencioso es peor que ninguno
        -- el usuario ve otra tipografia y no sabe por que.
        """
        cara = self._cara_de(font.family, font.bold)
        if cara is not None:
            return cara
        self._missing.add(font.family)
        for alternativa in (font.fallbacks or ()):
            cara = self._cara_de(alternativa, font.bold)
            if cara is not None:
                self._substitutions[font.family] = alternativa
                return cara
        return self._first_bundled() or self._any_system_mono()

    def _cara_de(self, familia, bold) -> Cara | None:
        entry = self.index().get(str(familia).lower())
        if not entry:
            return None
        # `or next(iter(...))`: una familia de una sola cara devuelve esa cara aunque
        # se haya pedido bold. Es lo correcto -- dibujar con la regular se parece mas
        # a lo pedido que caer al fallback generico -- pero conviene saberlo al leer
        # un panel donde el "bold" no se ve bold.
        return entry.get("bold" if bold else "regular") or next(iter(entry.values()))

    def substitutions(self) -> dict:
        """familia pedida -> familia con la que se dibujo.

        Es lo que explica lo que se ve en pantalla. "Falta X" solo dice que algo esta
        mal; "falta X, se uso Y" dice exactamente que estas mirando.
        """
        return dict(self._substitutions)

    def _first_bundled(self) -> Cara | None:
        if not BUNDLED.is_dir():
            return None
        for p in sorted(BUNDLED.iterdir()):
            if p.suffix.lower() in _EXTS and p.is_file():
                return Cara(p, 0)
        return None

    def _any_system_mono(self) -> Cara | None:
        for family in ("consolas", "cascadia mono", "courier new", "dejavu sans mono"):
            entry = self.index().get(family)
            if entry:
                return entry.get("regular") or next(iter(entry.values()))
        return None
