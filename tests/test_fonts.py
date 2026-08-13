import os
from pathlib import Path

import pytest
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
    (tmp_path / "copia.ttf").write_bytes(src.path.read_bytes())
    r2 = FontResolver(extra_dirs=[tmp_path])
    assert r2.index()["consolas"]["regular"].path == tmp_path / "copia.ttf"
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


def test_is_available_is_a_pure_query_independent_of_resolve():
    """is_available() no depende de haber llamado resolve() antes (a
    diferencia de missing(), que solo anota lo que un resolve() anterior
    efectivamente vio faltar). Es la razon por la que Renderer.warnings()
    la usa en vez de missing(): puede preguntarse antes de resolver nada,
    despues de resolver, o despues de un cache hit, y da siempre la misma
    respuesta -- es una funcion del indice, no del historial de llamadas.
    """
    r = FontResolver()
    assert r.is_available("Consolas") is True
    assert r.is_available("NoExisteEstaFuente") is False

    # Resolver la fuente ausente (que cachea el fallback) no cambia la
    # respuesta: sigue sin estar disponible.
    r.resolve(Font("NoExisteEstaFuente", 20))
    assert r.is_available("NoExisteEstaFuente") is False

    # Y un cache hit tampoco esconde una familia que si esta disponible.
    r.resolve(Font("Consolas", 20))
    r.resolve(Font("Consolas", 20))  # segunda vez: cache hit
    assert r.is_available("Consolas") is True


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


def test_missing_is_documented_as_the_wrong_channel():
    """El docstring del modulo decia que una familia ausente "se anota en
    missing(), para que el editor lo pueda avisar". Dejo de ser verdad cuando
    Renderer.warnings() paso a usar is_available(): missing() sigue existiendo y
    sigue teniendo el defecto del cortocircuito de cache -- resolve() devuelve
    de la cache en un hit sin volver a anotar --, asi que el docstring dirigia
    a un lector futuro justo al canal con el bug."""
    import vmaxpanel.render.fonts as mod
    assert "is_available" in (mod.__doc__ or ""), \
        "el docstring no menciona el canal correcto"
    assert "is_available" in (mod.FontResolver.missing.__doc__ or ""), \
        "missing() no advierte que no es el canal para avisos"


def test_warnings_do_not_duplicate_a_family_by_casing():
    """Dos alias con la misma familia en distinto casing daban dos lineas de
    aviso identicas para el usuario."""
    from vmaxpanel.layout import model
    from vmaxpanel.render.renderer import Renderer

    lay = model.Layout(
        1, "t", model.Size(64, 64), model.PanelCfg(),
        {"a": model.Font("NoExisteEstaFamilia", 12),
         "b": model.Font("noexisteestafamilia", 14),
         "c": model.Font("NOEXISTEESTAFAMILIA", 16)},
        model.Background(type="solid", color="#000000"), [])
    avisos = [w for w in Renderer(lay).warnings() if "font not found" in w]
    assert len(avisos) == 1, avisos


# --- caras multiples dentro de un .ttc ---

WINFONTS = Path(os.environ.get("WINDIR", "")) / "Fonts"


def _tiene(nombre):
    return (WINFONTS / nombre).exists()


@pytest.mark.skipif(not _tiene("msgothic.ttc"), reason="fuente de Windows ausente")
def test_a_family_that_only_exists_as_a_later_face_is_indexed():
    """msgothic.ttc empaqueta tres familias distintas -- MS Gothic, MS UI Gothic y
    MS PGothic -- como caras 0, 1 y 2 del mismo archivo. Indexando solo la cara 0
    las otras dos no existen para el motor: un perfil que las pida cae al
    fallback y el aviso dice que la familia falta, cuando esta instalada."""
    r = FontResolver()
    assert r.is_available("MS UI Gothic")
    assert r.is_available("MS PGothic")


@pytest.mark.skipif(not _tiene("Nirmala.ttc"), reason="fuente de Windows ausente")
def test_the_bold_face_of_a_ttc_resolves_to_that_face():
    """Nirmala.ttc trae Regular en la cara 0 y Bold en la 1. Sin el indice de
    cara, pedir bold devolvia el mismo archivo abierto en la cara 0 -- o sea la
    regular, silenciosamente."""
    r = FontResolver()
    negrita = r.resolve(Font("Nirmala UI", 20, bold=True))
    assert "bold" in negrita.getname()[1].lower()


@pytest.mark.skipif(not _tiene("cambria.ttc"), reason="fuente de Windows ausente")
def test_the_index_remembers_which_face_of_the_file_it_was():
    r = FontResolver()
    cara = r.index()["cambria math"]["regular"]
    assert cara.path.name.lower() == "cambria.ttc"
    assert cara.index == 1


# --- cadena de alternativas por alias ---


def test_a_font_falls_back_to_the_next_family_in_the_list():
    """El perfil Apex pide Franklin Gothic Medium Cond, que viene con OFFICE y no con
    Windows. En una maquina sin Office el layout se ve distinto y lo unico que hacia
    la app era avisar. Con la cadena, el perfil declara con que reemplazarla y el
    resultado se parece."""
    r = FontResolver()
    f = Font("NoExisteEnNingunaParte", 20,
             fallbacks=("TampocoExiste", "Consolas"))
    resuelta = r.resolve(f)
    assert "consol" in resuelta.getname()[0].lower()


def test_the_first_family_that_exists_wins():
    r = FontResolver()
    f = Font("Consolas", 20, fallbacks=("Arial",))
    assert "consol" in r.resolve(f).getname()[0].lower()


def test_a_family_with_no_fallback_that_exists_still_wins():
    r = FontResolver()
    assert "consol" in r.resolve(Font("Consolas", 20)).getname()[0].lower()


def test_when_nothing_in_the_chain_exists_it_is_reported():
    """Que el reemplazo sea silencioso seria peor que no tenerlo: el usuario ve otra
    tipografia y no sabe por que."""
    r = FontResolver()
    f = Font("NoExiste1", 20, fallbacks=("NoExiste2",))
    r.resolve(f)
    assert any("NoExiste1" in m for m in r.missing())


def test_the_substitution_that_was_used_is_reported(tmp_path):
    """No alcanza con decir "falta X": hay que decir con QUE se dibujo, que es lo que
    explica lo que se ve en pantalla."""
    r = FontResolver()
    f = Font("NoExisteEnNingunaParte", 20, fallbacks=("Consolas",))
    r.resolve(f)
    sust = r.substitutions()
    assert sust.get("NoExisteEnNingunaParte", "").lower().startswith("consol")


def test_a_compound_regular_style_is_not_treated_as_bold():
    """La whitelist era de match EXACTO, asi que "Regular Italic" no matcheaba
    "regular" y caia al heuristico del nombre de archivo -- donde un "bd" por
    casualidad la marca bold. Una familia podia perder su cara regular por el nombre
    del archivo."""
    assert FontResolver._is_bold("Regular Italic", "ERASBD") is False
    assert FontResolver._is_bold("Italic", "ERASBD") is False
    assert FontResolver._is_bold("Light Italic", "algobd") is False
    # y lo que SI es bold sigue siendo bold, aunque venga acompanado
    assert FontResolver._is_bold("Bold Italic", "cualquiera") is True
    assert FontResolver._is_bold("Semibold Condensed", "x") is True


def test_a_font_file_that_cannot_be_opened_falls_back_without_raising(tmp_path,
                                                                     monkeypatch):
    """La rama de fallo de ImageFont.truetype en resolve() estaba cubierta "por
    lectura" y por nada mas. Es la que evita que un TTF que se corrompio DESPUES del
    indexado -- o un disco de red que se cayo en el medio -- tumbe el render."""
    r = FontResolver()
    r.index()                                   # indexa con la fuente sana

    def truetype_roto(*a, **kw):
        raise OSError("el archivo se fue")

    monkeypatch.setattr(ImageFont, "truetype", truetype_roto)
    f = r.resolve(Font("Consolas", 20))
    assert f is not None                        # cayo al default de PIL, no levanto
