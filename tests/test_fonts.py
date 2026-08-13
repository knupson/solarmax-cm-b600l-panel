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
    # Consolas is monospaced: the advance width (getlength) is deliberately the
    # same in regular and bold, so the text does not shift. What distinguishes the
    # variant is the actual file/style loaded.
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
    """A font in extra_dirs is found by its real family, not by its filename."""
    r = FontResolver()
    src = r.index()["consolas"]["regular"]
    (tmp_path / "copia.ttf").write_bytes(src.path.read_bytes())
    r2 = FontResolver(extra_dirs=[tmp_path])
    assert r2.index()["consolas"]["regular"].path == tmp_path / "copia.ttf"
    assert not r2.missing()


def test_nonexistent_extra_dir_does_not_crash(tmp_path):
    """extra_dirs with a path that does not exist must not bring the indexing down."""
    r = FontResolver(extra_dirs=[tmp_path / "no-existe"])
    f = r.resolve(Font("Consolas", 20))
    assert f is not None
    assert "consolas" in r.index()


def test_unlistable_dir_is_skipped_once_and_index_is_still_cached(tmp_path, monkeypatch):
    """A directory that cannot be listed (permission denied, a network share down)
    must not stop the index from being finished and cached. If the error escaped
    _build_index(), self._index would never be assigned and EVERY resolve() would
    retry rebuilding the whole index -- on a panel at 1 fps with several text
    widgets, that reopens hundreds of files a second, forever. The broken directory
    also has to stay visible in unreadable_dirs(), the same way missing() makes
    absent families visible.

    There is no convenient way to make a real directory "unlistable" on Windows from
    inside a test, so it is simulated by patching Path.iterdir to raise
    PermissionError for that one directory.
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
    assert len(calls) == 1  # the broken directory was not retried


def test_unreadable_font_file_does_not_break_the_whole_index(tmp_path):
    """Un .ttf de 0 bytes (corrupto/truncado) en extra_dirs no debe tumbar
    the indexing of the other fonts, and the real family stays available.
    """
    (tmp_path / "roto.ttf").write_bytes(b"")
    r = FontResolver(extra_dirs=[tmp_path])
    assert "consolas" in r.index()
    f = r.resolve(Font("Consolas", 20))
    assert "consolas" in f.getname()[0].lower()


def test_is_available_is_a_pure_query_independent_of_resolve():
    """is_available() no depende de haber llamado resolve() antes (a
    unlike missing(), which only records what an earlier resolve() actually saw
    missing). It is the reason Renderer.warnings() uses it rather than missing(): it
    can be asked before resolving anything, after resolving, or after a cache hit,
    and always gives the same answer -- it is a function of the index, not of the
    history of calls.
    """
    r = FontResolver()
    assert r.is_available("Consolas") is True
    assert r.is_available("NoExisteEstaFuente") is False

    # Resolving the missing font (which caches the fallback) does not change the
    # answer: it is still unavailable.
    r.resolve(Font("NoExisteEstaFuente", 20))
    assert r.is_available("NoExisteEstaFuente") is False

    # And a cache hit does not hide a family that IS available either.
    r.resolve(Font("Consolas", 20))
    r.resolve(Font("Consolas", 20))  # segunda vez: cache hit
    assert r.is_available("Consolas") is True


def test_filename_bold_hint_does_not_override_explicit_regular_style():
    """Some system files have 'bd' in their name by coincidence
    (ERASBD.TTF, webdings.ttf) aunque su estilo real sea Regular. El estilo
    the font itself reports has to beat the filename, otherwise a family with real
    bold+regular variants could lose its regular face because the regular's filename
    trips the heuristic.
    """
    assert FontResolver._is_bold("Regular", "ERASBD") is False
    assert FontResolver._is_bold("Regular", "webdings") is False
    assert FontResolver._is_bold("Regular", "consola") is False
    assert FontResolver._is_bold("Bold", "consolab") is True
    # with no explicit style, the filename is still the only clue
    assert FontResolver._is_bold("", "somethingbold") is True


def test_missing_is_documented_as_the_wrong_channel():
    """The module docstring used to say that a missing family "is recorded in
    missing(), so the editor can report it". That stopped being true when
    Renderer.warnings() switched to is_available(): missing() still exists and still
    has the cache short-circuit defect -- resolve() returns from the cache on a hit
    without recording again -- so the docstring pointed a future reader straight at
    the channel with the bug."""
    import vmaxpanel.render.fonts as mod
    assert "is_available" in (mod.__doc__ or ""), \
        "the docstring does not mention the correct channel"
    assert "is_available" in (mod.FontResolver.missing.__doc__ or ""), \
        "missing() does not warn that it is not the channel for warnings"


def test_warnings_do_not_duplicate_a_family_by_casing():
    """Two aliases with the same family in different casing produced two identical
    warning lines for the user."""
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
    MS PGothic -- as faces 0, 1 and 2 of the same file. Indexing only face 0, the
    other two do not exist for the engine: a profile asking for them falls back and
    the warning says the family is missing when it is installed."""
    r = FontResolver()
    assert r.is_available("MS UI Gothic")
    assert r.is_available("MS PGothic")


@pytest.mark.skipif(not _tiene("Nirmala.ttc"), reason="fuente de Windows ausente")
def test_the_bold_face_of_a_ttc_resolves_to_that_face():
    """Nirmala.ttc carries Regular in face 0 and Bold in face 1. Without the face
    index, asking for bold returned the same file opened at face 0 -- that is, the
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
    """The Apex profile asks for Franklin Gothic Medium Cond, which ships with OFFICE
    and not with Windows. On a machine without Office the layout looks different and
    all the app used to do was warn. With the chain, the profile declares what to
    replace it with and the
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
    """A silent substitution would be worse than none: the user sees a different
    typeface and does not know why."""
    r = FontResolver()
    f = Font("NoExiste1", 20, fallbacks=("NoExiste2",))
    r.resolve(f)
    assert any("NoExiste1" in m for m in r.missing())


def test_the_substitution_that_was_used_is_reported(tmp_path):
    """Saying "X is missing" is not enough: it has to say WHAT it was drawn with,
    which is what explains what is on screen."""
    r = FontResolver()
    f = Font("NoExisteEnNingunaParte", 20, fallbacks=("Consolas",))
    r.resolve(f)
    sust = r.substitutions()
    assert sust.get("NoExisteEnNingunaParte", "").lower().startswith("consol")


def test_a_compound_regular_style_is_not_treated_as_bold():
    """The whitelist was an EXACT match, so "Regular Italic" did not match "regular"
    and fell through to the filename heuristic -- where a coincidental "bd" marks it
    bold. A family could lose its regular face because of a filename."""
    assert FontResolver._is_bold("Regular Italic", "ERASBD") is False
    assert FontResolver._is_bold("Italic", "ERASBD") is False
    assert FontResolver._is_bold("Light Italic", "algobd") is False
    # and what IS bold is still bold, even with other words alongside
    assert FontResolver._is_bold("Bold Italic", "cualquiera") is True
    assert FontResolver._is_bold("Semibold Condensed", "x") is True


def test_a_font_file_that_cannot_be_opened_falls_back_without_raising(tmp_path,
                                                                     monkeypatch):
    """The failure branch of ImageFont.truetype in resolve() was covered "by reading
    it" and by nothing else. It is what keeps a TTF that got corrupted AFTER indexing
    -- or a network share that went down midway -- from bringing the render down."""
    r = FontResolver()
    r.index()                                   # index with the healthy font

    def truetype_roto(*a, **kw):
        raise OSError("the file went away")

    monkeypatch.setattr(ImageFont, "truetype", truetype_roto)
    f = r.resolve(Font("Consolas", 20))
    assert f is not None                        # cayo al default de PIL, no levanto
