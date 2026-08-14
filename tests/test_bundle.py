"""Exporting and importing a profile as a single file.

A profile is not just its JSON: it references assets (the background's video or
image, a sequence's folder) and names fonts by family. Copying the bare .json to
another machine gives a panel with a degraded background and different fonts, with
nobody understanding why. The bundle carries the JSON and the assets together, and
reports which fonts are absent on the importing machine.
"""
import json
import zipfile

import pytest

from vmaxpanel import bundle
from tests.test_schema import MINIMAL


@pytest.fixture
def entorno(tmp_path):
    """Un proyecto de juguete: perfiles, assets y un perfil valido adentro."""
    perfiles = tmp_path / "profiles"
    assets = tmp_path / "assets"
    perfiles.mkdir()
    assets.mkdir()
    (assets / "fondo.mp4").write_bytes(b"not really an mp4 but it will do")
    raw = dict(MINIMAL)
    raw["name"] = "Mio"
    raw["background"] = {"type": "video", "src": "fondo.mp4", "fit": "cover",
                         "fps": 30, "color": "#000000"}
    perfil = perfiles / "mio.json"
    perfil.write_text(json.dumps(raw), encoding="utf-8")
    return perfil, perfiles, assets


# --- exportar ---


def test_exporting_puts_the_profile_and_its_asset_in_the_zip(entorno, tmp_path):
    perfil, _, assets = entorno
    zip_ = tmp_path / "mio.vmaxpanel"
    info = bundle.export_profile(perfil, zip_, assets_dir=assets)
    assert zip_.exists()
    with zipfile.ZipFile(zip_) as z:
        nombres = set(z.namelist())
    assert "perfil.json" in nombres
    assert "assets/fondo.mp4" in nombres
    assert "bundle.json" in nombres
    assert info["assets"] == ["fondo.mp4"]


def test_the_manifest_records_what_the_profile_needs(entorno, tmp_path):
    perfil, _, assets = entorno
    bundle.export_profile(perfil, tmp_path / "b.vmaxpanel", assets_dir=assets)
    with zipfile.ZipFile(tmp_path / "b.vmaxpanel") as z:
        man = json.loads(z.read("bundle.json"))
    assert man["name"] == "Mio"
    assert man["designed_for"] == {"width": 320, "height": 1480}
    assert "Consolas" in man["fonts"]        # the families the profile names
    assert man["format"] == bundle.FORMATO


def test_exporting_an_invalid_profile_refuses(tmp_path):
    roto = tmp_path / "roto.json"
    roto.write_text("{ no", encoding="utf-8")
    with pytest.raises(bundle.BundleError) as e:
        bundle.export_profile(roto, tmp_path / "b.vmaxpanel", assets_dir=tmp_path)
    assert "profile" in str(e.value).lower()


def test_a_missing_asset_is_reported_but_does_not_block_the_export(entorno, tmp_path):
    """The background can be missing and the profile is still useful: the engine
    degrades to a flat colour. Blocking the export over that leaves the user unable
    to share their layout because of a file they may not care about."""
    perfil, _, assets = entorno
    (assets / "fondo.mp4").unlink()
    info = bundle.export_profile(perfil, tmp_path / "b.vmaxpanel", assets_dir=assets)
    assert info["faltantes"] == ["fondo.mp4"]
    assert info["assets"] == []


def test_a_sequence_folder_is_exported_whole(tmp_path):
    perfiles, assets = tmp_path / "p", tmp_path / "a"
    perfiles.mkdir()
    (assets / "cuadros").mkdir(parents=True)
    for i in range(3):
        (assets / "cuadros" / f"{i}.png").write_bytes(b"x")
    raw = dict(MINIMAL)
    raw["background"] = {"type": "sequence", "src": "cuadros", "fit": "cover",
                         "fps": 10, "color": "#000000"}
    p = perfiles / "s.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    bundle.export_profile(p, tmp_path / "b.vmaxpanel", assets_dir=assets)
    with zipfile.ZipFile(tmp_path / "b.vmaxpanel") as z:
        assert sum(n.startswith("assets/cuadros/") for n in z.namelist()) == 3


# --- importar ---


def test_importing_restores_the_profile_and_the_asset(entorno, tmp_path):
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)

    destino_p, destino_a = tmp_path / "p2", tmp_path / "a2"
    destino_p.mkdir()
    destino_a.mkdir()
    info = bundle.import_bundle(zip_, destino_p, destino_a)
    assert (destino_p / "mio.json").exists()
    assert (destino_a / "fondo.mp4").read_bytes() == b"not really an mp4 but it will do"
    assert info["profile"].name == "mio.json"


def test_importing_validates_before_writing_anything(tmp_path):
    """A bundle with an invalid profile must not leave assets half-copied in the
    user's folder: it is validated first and nothing is written."""
    zip_ = tmp_path / "malo.vmaxpanel"
    with zipfile.ZipFile(zip_, "w") as z:
        z.writestr("perfil.json", json.dumps({"name": "x"}))
        z.writestr("assets/algo.bin", b"x")
    destino_p, destino_a = tmp_path / "p", tmp_path / "a"
    destino_p.mkdir()
    destino_a.mkdir()
    with pytest.raises(bundle.BundleError):
        bundle.import_bundle(zip_, destino_p, destino_a)
    assert list(destino_p.iterdir()) == []
    assert list(destino_a.iterdir()) == []


def test_an_entry_that_escapes_the_destination_is_refused(tmp_path):
    """Zip-slip. A member '..\\..\\something' writes wherever whoever built the zip
    likes, and this process runs elevated (the task uses HighestAvailable). It is
    the same class of hole safe_asset_path() already closes for the src field, now
    on the other path by which somebody else's files come in."""
    zip_ = tmp_path / "malicioso.vmaxpanel"
    with zipfile.ZipFile(zip_, "w") as z:
        z.writestr("perfil.json", json.dumps(MINIMAL))
        z.writestr("assets/../../escapado.txt", b"x")
    destino_p, destino_a = tmp_path / "p", tmp_path / "a"
    destino_p.mkdir()
    destino_a.mkdir()
    with pytest.raises(bundle.BundleError) as e:
        bundle.import_bundle(zip_, destino_p, destino_a)
    assert "escapes" in str(e.value)
    assert not (tmp_path / "escapado.txt").exists()


def test_an_absolute_entry_is_refused(tmp_path):
    zip_ = tmp_path / "abs.vmaxpanel"
    with zipfile.ZipFile(zip_, "w") as z:
        z.writestr("perfil.json", json.dumps(MINIMAL))
        z.writestr("C:/Windows/system32/algo.dll", b"x")
    (tmp_path / "p").mkdir()
    (tmp_path / "a").mkdir()
    with pytest.raises(bundle.BundleError):
        bundle.import_bundle(zip_, tmp_path / "p", tmp_path / "a")


def test_an_oversized_member_is_refused(tmp_path, monkeypatch):
    """Zip bomb: a member that declares itself small and expands to gigabytes. It is
    cut off by the declared size, which is what can be known without
    decompressing."""
    monkeypatch.setattr(bundle, "MAX_MIEMBRO", 100)
    zip_ = tmp_path / "bomba.vmaxpanel"
    with zipfile.ZipFile(zip_, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("perfil.json", json.dumps(MINIMAL))
        z.writestr("assets/gordo.bin", b"0" * 5000)
    (tmp_path / "p").mkdir()
    (tmp_path / "a").mkdir()
    with pytest.raises(bundle.BundleError) as e:
        bundle.import_bundle(zip_, tmp_path / "p", tmp_path / "a")
    assert "too large" in str(e.value)


def test_importing_does_not_overwrite_by_default(entorno, tmp_path):
    """Importing must not silently overwrite the profile the user has running: their
    layout is their own work, and a repeated name is the normal case when two
    personas exportan 'apex'."""
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    with pytest.raises(bundle.BundleError) as e:
        bundle.import_bundle(zip_, perfiles, assets)
    assert "already exists" in str(e.value)


def test_importing_can_rename_instead_of_overwriting(entorno, tmp_path):
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    info = bundle.import_bundle(zip_, perfiles, assets, si_existe="renombrar")
    assert info["profile"].name == "mio-2.json"
    assert (perfiles / "mio.json").exists()          # the original intact


def test_importing_can_overwrite_when_asked(entorno, tmp_path):
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    perfil.write_text(json.dumps({"roto": True}), encoding="utf-8")
    info = bundle.import_bundle(zip_, perfiles, assets, si_existe="pisar")
    assert info["profile"] == perfil
    assert json.loads(perfil.read_text(encoding="utf-8"))["name"] == "Mio"


def test_importing_reports_the_fonts_that_are_missing_here(entorno, tmp_path):
    """The number one reason somebody else's profile looks different. It cannot be
    fixed -- fonts are not packaged, they belong to Microsoft -- but it can be said,
    which is the difference between "it looks wrong" and "you are missing this
    font"."""
    perfil, perfiles, assets = entorno
    raw = json.loads(perfil.read_text(encoding="utf-8"))
    raw["fonts"] = {"m": {"family": "NoExisteEnNingunaMaquina", "size": 14}}
    for w in raw["widgets"]:
        if "font" in w:
            w["font"] = "m"
    perfil.write_text(json.dumps(raw), encoding="utf-8")
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    info = bundle.import_bundle(zip_, tmp_path / "p2", tmp_path / "a2")
    assert info["fuentes_faltantes"] == ["NoExisteEnNingunaMaquina"]


def test_the_destination_folders_are_created_if_needed(entorno, tmp_path):
    perfil, _, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    bundle.import_bundle(zip_, tmp_path / "nuevo" / "p", tmp_path / "nuevo" / "a")
    assert (tmp_path / "nuevo" / "p" / "mio.json").exists()


def test_a_zip_that_is_not_a_bundle_is_refused(tmp_path):
    zip_ = tmp_path / "cualquiera.zip"
    with zipfile.ZipFile(zip_, "w") as z:
        z.writestr("hola.txt", b"x")
    with pytest.raises(bundle.BundleError) as e:
        bundle.import_bundle(zip_, tmp_path / "p", tmp_path / "a")
    assert "perfil.json" in str(e.value)


def test_a_file_that_is_not_a_zip_is_refused(tmp_path):
    falso = tmp_path / "no-es-zip.vmaxpanel"
    falso.write_bytes(b"esto no es un zip")
    with pytest.raises(bundle.BundleError):
        bundle.import_bundle(falso, tmp_path / "p", tmp_path / "a")


def test_a_roundtrip_keeps_the_profile_byte_identical(entorno, tmp_path):
    """Exporting and importing must not reformat the profile: the user has to be
    able to compare theirs with the one that comes back and see they are the
    same."""
    perfil, _, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    info = bundle.import_bundle(zip_, tmp_path / "p2", tmp_path / "a2")
    assert info["profile"].read_bytes() == perfil.read_bytes()


# --- entrada por linea de comandos ---


def test_the_cli_exports_the_current_profile(entorno, tmp_path, capsys, monkeypatch):
    perfil, _, assets = entorno
    from vmaxpanel import cli
    monkeypatch.setattr(cli, "assets_dir", lambda: assets)
    salida = tmp_path / "salida.vmaxpanel"
    code = cli.main(["--profile", str(perfil), "--exportar", str(salida)])
    assert code == 0
    assert salida.exists()
    assert "fondo.mp4" in capsys.readouterr().out


def test_the_cli_import_reports_where_it_landed(entorno, tmp_path, capsys, monkeypatch):
    perfil, perfiles, assets = entorno
    from vmaxpanel import cli
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    destino_p, destino_a = tmp_path / "p2", tmp_path / "a2"
    monkeypatch.setattr(cli, "profiles_dir", lambda: destino_p)
    monkeypatch.setattr(cli, "assets_dir", lambda: destino_a)
    code = cli.main(["--importar", str(zip_)])
    assert code == 0
    assert (destino_p / "mio.json").exists()
    assert "mio.json" in capsys.readouterr().out


def test_the_cli_import_of_a_broken_bundle_fails_loudly(tmp_path, capsys):
    from vmaxpanel import cli
    falso = tmp_path / "x.vmaxpanel"
    falso.write_bytes(b"no soy un zip")
    assert cli.main(["--importar", str(falso)]) == 2
    assert "bundle" in capsys.readouterr().out.lower()


def test_the_cli_export_refuses_to_overwrite_silently(entorno, tmp_path, capsys,
                                                     monkeypatch):
    """Exporting twice to the same name must not overwrite the previous bundle
    without warning: it may be the one the user already sent to somebody."""
    perfil, _, assets = entorno
    from vmaxpanel import cli
    monkeypatch.setattr(cli, "assets_dir", lambda: assets)
    salida = tmp_path / "s.vmaxpanel"
    # Deliberately with the old `--exportar` flag and not `--export`: the Spanish
    # names were kept as aliases so already-written scripts do not break, and this
    # is the test that proves it.
    assert cli.main(["--profile", str(perfil), "--exportar", str(salida)]) == 0
    assert cli.main(["--profile", str(perfil), "--exportar", str(salida)]) == 2
    assert "already exists" in capsys.readouterr().out


def test_a_profile_with_crlf_survives_the_roundtrip(entorno, tmp_path):
    """This repo's profiles carry CRLF -- loader.save_raw writes them that way on
    Windows. Reading them as text translates the line endings to LF, so the profile
    coming back out of the bundle was 60 bytes smaller than the original and "the
    same" was a lie. It is read and written in bytes.
    """
    perfil, _, assets = entorno
    # With indent so it really has line breaks: the single-line profile in the
    # fixture exercises nothing.
    crudo = json.dumps(json.loads(perfil.read_text(encoding="utf-8")), indent=1)
    perfil.write_bytes(crudo.replace("\n", "\r\n").encode("utf-8"))
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    info = bundle.import_bundle(zip_, tmp_path / "p2", tmp_path / "a2")
    assert info["profile"].read_bytes() == perfil.read_bytes()
    assert b"\r\n" in info["profile"].read_bytes()


def test_importing_over_a_live_profile_is_atomic(entorno, tmp_path, monkeypatch):
    """The profile being overwritten may be the one the engine is reading RIGHT NOW:
    the hot reload re-reads it by content hash, so a half-finished write_bytes can be
    read truncated. `loader.save_raw` uses a temp file plus a replace for this very
    reason; importing had to do the same.

    It is tested by making the replace fail: if the new content were written
    straight over the destination, the old profile would already be destroyed by the
    time the failure
    ocurre.
    """
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    viejo = b'{"i am": "the one that was running"}'
    perfil.write_bytes(viejo)

    import os
    real = os.replace

    def replace_que_falla(a, b, *args, **kw):
        if str(b).endswith("mio.json"):
            raise OSError("disco lleno justo ahora")
        return real(a, b, *args, **kw)

    monkeypatch.setattr(os, "replace", replace_que_falla)
    with pytest.raises(bundle.BundleError):
        bundle.import_bundle(zip_, perfiles, assets, si_existe="pisar")

    assert perfil.read_bytes() == viejo, "it destroyed the profile that was running"
    assert not list(perfiles.glob("*.tmp")), "it left the temp file behind"


def test_an_asset_is_also_written_atomically(entorno, tmp_path, monkeypatch):
    """The same reason from the other side: ffmpeg may be reading the video being
    overwritten, and
    un archivo a medio escribir es un fondo roto en vez de uno nuevo."""
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    (assets / "fondo.mp4").write_bytes(b"the video that is playing")

    import os
    real = os.replace

    def replace_que_falla(a, b, *args, **kw):
        if str(b).endswith("fondo.mp4"):
            raise OSError("disco lleno justo ahora")
        return real(a, b, *args, **kw)

    monkeypatch.setattr(os, "replace", replace_que_falla)
    with pytest.raises(bundle.BundleError):
        bundle.import_bundle(zip_, perfiles, assets, si_existe="pisar")
    assert (assets / "fondo.mp4").read_bytes() == b"the video that is playing"
    assert not list(assets.glob("*.tmp"))
