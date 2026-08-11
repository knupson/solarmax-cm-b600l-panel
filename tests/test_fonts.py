from pathlib import Path

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
    # Consolas es monoespaciada: el ancho de avance (getlength) es igual
    # en regular y en bold a proposito, para que el texto no se corra.
    # Lo que distingue la variante es el archivo/estilo real cargado.
    r = FontResolver()
    reg = r.resolve(Font("Consolas", 40, bold=False))
    bold = r.resolve(Font("Consolas", 40, bold=True))
    assert reg.getname() != bold.getname()
    assert bold.getname()[1].lower() == "bold"


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


def test_nonexistent_extra_dir_does_not_crash(tmp_path):
    """extra_dirs con una ruta que no existe no debe tumbar el indexado."""
    r = FontResolver(extra_dirs=[tmp_path / "no-existe"])
    f = r.resolve(Font("Consolas", 20))
    assert f is not None
    assert "consolas" in r.index()


def test_unlistable_dir_is_skipped_once_and_index_is_still_cached(tmp_path, monkeypatch):
    """Un directorio que no se puede listar (permiso denegado, recurso de
    red caido) no debe impedir que el indice se termine de construir y se
    cachee. Si el error escapara de _build_index(), self._index nunca se
    asignaria y CADA resolve() reintentaria reconstruir el indice completo
    -- en un panel a 1 fps con varios widgets de texto, eso reabre cientos
    de archivos por segundo, para siempre. Ademas el directorio roto tiene
    que quedar visible en unreadable_dirs(), igual que missing() deja ver
    las familias ausentes.

    No hay forma comoda de dejar un directorio real "no listable" en
    Windows dentro de un test, asi que se simula parcheando Path.iterdir
    para que ese directorio en particular levante PermissionError.
    """
    broken = tmp_path / "roto"
    broken.mkdir()

    original_iterdir = Path.iterdir
    calls = []

    def fake_iterdir(self):
        if self == broken:
            calls.append(1)
            raise PermissionError("acceso denegado (simulado)")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)

    r = FontResolver(extra_dirs=[broken])
    idx1 = r.index()
    assert "consolas" in idx1
    assert broken in r.unreadable_dirs()
    assert len(calls) == 1

    idx2 = r.index()
    assert idx2 is idx1
    r.resolve(Font("Consolas", 20))
    assert len(calls) == 1  # no se reintento el directorio roto


def test_unreadable_font_file_does_not_break_the_whole_index(tmp_path):
    """Un .ttf de 0 bytes (corrupto/truncado) en extra_dirs no debe tumbar
    el indexado de las demas fuentes, y la familia real sigue disponible.
    """
    (tmp_path / "roto.ttf").write_bytes(b"")
    r = FontResolver(extra_dirs=[tmp_path])
    assert "consolas" in r.index()
    f = r.resolve(Font("Consolas", 20))
    assert "consolas" in f.getname()[0].lower()


def test_filename_bold_hint_does_not_override_explicit_regular_style():
    """Algunos archivos del sistema tienen 'bd' en el nombre por casualidad
    (ERASBD.TTF, webdings.ttf) aunque su estilo real sea Regular. El estilo
    que reporta la propia fuente tiene que ganarle al nombre de archivo,
    sino una familia con variantes real bold+regular podria perder su cara
    regular porque el nombre de archivo de la regular dispara el heuristico.
    """
    assert FontResolver._is_bold("Regular", "ERASBD") is False
    assert FontResolver._is_bold("Regular", "webdings") is False
    assert FontResolver._is_bold("Regular", "consola") is False
    assert FontResolver._is_bold("Bold", "consolab") is True
    # sin estilo explicito, el nombre de archivo sigue siendo la unica pista
    assert FontResolver._is_bold("", "somethingbold") is True
