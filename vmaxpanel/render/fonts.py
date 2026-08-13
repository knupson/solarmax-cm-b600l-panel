"""Resolves font aliases to real files, by family name.

No TTFs are bundled: Consolas and the rest belong to Microsoft and are not
redistributable. Families are looked up in assets/fonts/ first and then among the
system's fonts.

A missing family falls back. **To report that, `is_available()` is used, not
`missing()`**: missing() only records what a resolve() actually saw missing, and
resolve() returns from the cache on a hit without passing through there again, so
a long-lived resolver can stay silent about a family that is still missing.
is_available() is a query against the index, not a history of calls, and gives the
same answer the first time and the hundredth. It never raises: somebody else's
layout must not be able to bring the render down.

A .ttc packs several faces into one file and **all of them are indexed**. An
earlier version read only face 0, on the argument that on Windows the weights come
in separate files (msjh.ttc / msjhbd.ttc). That is true for weights and false for
families: msgothic.ttc carries MS Gothic, MS UI Gothic and MS PGothic as faces 0,
1 and 2; cambria.ttc hides Cambria Math in face 1; simsun.ttc hides NSimSun. With
face 0 alone those families did not exist for the engine -- a profile asking for
them fell back, and the warning said they were missing while they were installed.
And Nirmala.ttc does pack its bold as an extra face, so the case that "does not
happen" was on the machine too. The cost is opening each .ttc a few more times,
once per process.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

BUNDLED = Path(__file__).resolve().parent.parent / "assets" / "fonts"
# Cap on faces per file. The fattest .ttc on Windows has 6 (Nirmala); 16 leaves
# plenty of room and bounds the scan in case some odd file returns faces forever
# instead of raising.
MAX_CARAS = 16
_EXTS = (".ttf", ".otf", ".ttc")
_BOLD_HINTS = ("bold", "bd", "black", "heavy", "semibold")


@dataclass(frozen=True)
class Cara:
    """A font file and the face inside it.

    `index` is almost always 0 -- a .ttf has a single face -- but for a .ttc it is
    the only way to reopen the right face: PIL takes the index when opening, not
    afterwards.
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
    """Looks fonts up by family and caches the result. It does not require
    Windows: elsewhere there simply are no system directories and everything falls
    back to the bundled font or to PIL's default.
    """

    def __init__(self, extra_dirs: list[Path] | None = None):
        # Precedence: extra_dirs > bundled > system
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
        """Directories in _dirs that exist but could not be listed (permission
        denied, a network share down, and so on). It does not bring the indexing
        down -- they are skipped and the rest of the index is built anyway -- but
        it is a real misconfiguration, and that is why it is visible here, the same
        way missing() makes absent families visible.
        """
        return set(self._unreadable_dirs)

    def is_available(self, family: str) -> bool:
        """True if `family` appears in the font index (bundled or system),
        regardless of the weight (bold/regular) asked for and of whether resolve()
        was ever called for it.

        Unlike missing() -- which only records what an earlier resolve() actually
        saw missing, and therefore goes quiet on a cache hit -- this is a pure
        function of the index: it can be called before resolving anything and gives
        the same answer afterwards. It is what Renderer.warnings() uses so it does
        not have to accumulate or reset any per-layout state of its own.
        """
        return bool(self.index().get(family.lower()))

    def _build_index(self) -> dict[str, dict[str, Cara]]:
        idx: dict[str, dict[str, Cara]] = {}
        for d in self._dirs:
            try:
                if not d.is_dir():
                    # The caller can pass anything in extra_dirs; a directory that
                    # does not exist must not bring the indexing down.
                    continue
                paths = sorted(d.iterdir())
            except OSError:
                # Permission denied, a network share down, and so on. If this
                # escaped _build_index(), self._index would never be assigned and
                # EVERY resolve() would retry rebuilding the whole index (every
                # directory, every file) over and over -- on a panel at 1 fps with
                # several text widgets that is hundreds of file opens a second,
                # forever. The broken directory is skipped, recorded, and the rest
                # of the index is built and cached normally.
                self._unreadable_dirs.add(d)
                continue
            for path in paths:
                if path.suffix.lower() not in _EXTS or not path.is_file():
                    continue
                for cara, family, style in self._caras(path):
                    slot = "bold" if self._is_bold(style, path.stem) else "regular"
                    # setdefault: the first directory in _dirs that carries a
                    # family/slot wins. Since extra_dirs comes first in the list, a
                    # copy there overrides the system one for the same family.
                    idx.setdefault(family.lower(), {}).setdefault(slot, cara)
        return idx

    @staticmethod
    def _caras(path: Path):
        """[(Cara, family, style)] for every face in the file.

        Only .ttc files can carry more than one, so for the rest this opens the
        file once and stops: asking for face 1 of a .ttf raises, and that except is
        what ends the loop.
        """
        fuera = []
        for i in range(MAX_CARAS):
            try:
                f = ImageFont.truetype(os.fspath(path), 12, index=i)
                family, style = f.getname()
            except Exception:
                # The file ended, or it is corrupt/unreadable. Neither can bring
                # the whole indexing down: it stops and moves on to the rest of the
                # files, keeping the faces that did read.
                break
            fuera.append((Cara(path, i), family or path.stem, style or ""))
        return fuera

    @staticmethod
    def _is_bold(style: str, stem: str) -> bool:
        """Whether this face is its family's bold variant.

        **The style the font declares decides; the file name is only the fallback.**
        If the font states any style at all -- "Regular", "Italic", "Light Italic",
        "Semilight" -- it is believed: it is bold only if that style says so. The
        file name is consulted only when there is no style.

        This used to be an exact-match whitelist ("regular", "light", ...), which is
        why "Regular Italic" matched nothing and fell through to the name heuristic:
        there a coincidental "bd" (ERASBD.TTF is "Eras Bold ITC" with style
        *Regular*; webdings.ttf has "bd" inside it) marked the face as bold and the
        family lost its regular. The new rule does not need to enumerate the styles
        that exist, which was the underlying problem.
        """
        style_l = style.lower().strip()
        if style_l:
            return any(h in style_l for h in _BOLD_HINTS)
        return any(h in stem.lower() for h in _BOLD_HINTS)

    def missing(self) -> set[str]:
        """Families that SOME resolve() saw missing. A history, not a state.

        This is not the channel for warning the user -- is_available() is. resolve()
        returns from the cache on a hit without recording again, so a family that is
        still missing may not show up here. It is kept because it is useful for
        diagnosing what the layout asked for, not what the system has.
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
            # A broken extra_dir, a permission denied, whatever: it never brings the
            # render down, it is recorded as missing and falls back to the default.
            self._missing.add(font.family)
            cara = None

        resolved = None
        for intento in (lambda: cara.open(size) if cara is not None else None,
                        lambda: ImageFont.load_default(size),
                        # Last resort WITHOUT size: modern Pillow's
                        # load_default(size) opens a font through truetype
                        # internally, so if truetype is what fails -- a corrupt TTF,
                        # a network share down -- the fallback fails for the SAME
                        # reason and the exception escaped resolve(), killing the
                        # render. The size-less variant (load_default_imagefont in
                        # Pillow 12) returns the embedded bitmap and opens no file.
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
        """The face for this Font: its family, or the first of `fallbacks` that exists.

        The chain is declared by the PROFILE, not by the resolver: only whoever
        designed the layout knows what looks like what they wanted. The resolver just
        walks it and records what it ended up drawing with, because a silent
        substitution is worse than none -- the user sees a different typeface and
        does not know why.
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
        # `or next(iter(...))`: a single-face family returns that face even when bold
        # was asked for. That is right -- drawing with the regular looks closer to
        # what was asked than falling back to the generic font -- but it is worth
        # knowing when reading a panel where the "bold" does not look bold.
        return entry.get("bold" if bold else "regular") or next(iter(entry.values()))

    def substitutions(self) -> dict:
        """Family asked for -> family it was drawn with.

        This is what explains what is on screen. "X is missing" only says something
        is wrong; "X is missing, Y was used" says exactly what you are looking at.
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
