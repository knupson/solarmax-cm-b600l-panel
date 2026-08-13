"""Exportar e importar un perfil como un solo archivo.

Un perfil no es solo su JSON: referencia assets (el video o la imagen del fondo,
la carpeta de una secuencia) y nombra fuentes por familia. Copiar el .json suelto
a otra maquina da un panel con el fondo degradado y las fuentes cambiadas, sin que
nadie entienda por que. El bundle lleva el JSON y los assets juntos, y avisa que
fuentes no estan en la maquina que importa.
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
    (assets / "fondo.mp4").write_bytes(b"no es un mp4 pero alcanza")
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
    assert "Consolas" in man["fonts"]        # las familias que el perfil nombra
    assert man["format"] == bundle.FORMATO


def test_exporting_an_invalid_profile_refuses(tmp_path):
    roto = tmp_path / "roto.json"
    roto.write_text("{ no", encoding="utf-8")
    with pytest.raises(bundle.BundleError) as e:
        bundle.export_profile(roto, tmp_path / "b.vmaxpanel", assets_dir=tmp_path)
    assert "perfil" in str(e.value).lower()


def test_a_missing_asset_is_reported_but_does_not_block_the_export(entorno, tmp_path):
    """El fondo puede faltar y el perfil sigue siendo util: el motor degrada a
    color plano. Bloquear la exportacion por eso deja al usuario sin poder
    compartir su layout por un archivo que quizas no le importa."""
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
    assert (destino_a / "fondo.mp4").read_bytes() == b"no es un mp4 pero alcanza"
    assert info["profile"].name == "mio.json"


def test_importing_validates_before_writing_anything(tmp_path):
    """Un bundle con un perfil invalido no puede dejar assets a medio copiar en la
    carpeta del usuario: se valida primero y no se escribe nada."""
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
    """Zip-slip. Un miembro '..\\..\\algo' escribe donde quiera el que armo el zip,
    y este proceso corre elevado (la tarea usa HighestAvailable). Es la misma clase
    de agujero que safe_asset_path() ya cierra para el campo src, ahora en el
    otro camino por el que entran archivos ajenos."""
    zip_ = tmp_path / "malicioso.vmaxpanel"
    with zipfile.ZipFile(zip_, "w") as z:
        z.writestr("perfil.json", json.dumps(MINIMAL))
        z.writestr("assets/../../escapado.txt", b"x")
    destino_p, destino_a = tmp_path / "p", tmp_path / "a"
    destino_p.mkdir()
    destino_a.mkdir()
    with pytest.raises(bundle.BundleError) as e:
        bundle.import_bundle(zip_, destino_p, destino_a)
    assert "escapa" in str(e.value)
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
    """Zip bomb: un miembro que se declara chico y descomprime a gigas. Se corta
    por el tamano declarado, que es lo que se puede saber sin descomprimir."""
    monkeypatch.setattr(bundle, "MAX_MIEMBRO", 100)
    zip_ = tmp_path / "bomba.vmaxpanel"
    with zipfile.ZipFile(zip_, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("perfil.json", json.dumps(MINIMAL))
        z.writestr("assets/gordo.bin", b"0" * 5000)
    (tmp_path / "p").mkdir()
    (tmp_path / "a").mkdir()
    with pytest.raises(bundle.BundleError) as e:
        bundle.import_bundle(zip_, tmp_path / "p", tmp_path / "a")
    assert "grande" in str(e.value)


def test_importing_does_not_overwrite_by_default(entorno, tmp_path):
    """Importar no puede pisar en silencio el perfil que el usuario tiene andando:
    su layout es trabajo suyo, y un nombre repetido es lo normal cuando dos
    personas exportan 'apex'."""
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    with pytest.raises(bundle.BundleError) as e:
        bundle.import_bundle(zip_, perfiles, assets)
    assert "ya existe" in str(e.value)


def test_importing_can_rename_instead_of_overwriting(entorno, tmp_path):
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    info = bundle.import_bundle(zip_, perfiles, assets, si_existe="renombrar")
    assert info["profile"].name == "mio-2.json"
    assert (perfiles / "mio.json").exists()          # el original intacto


def test_importing_can_overwrite_when_asked(entorno, tmp_path):
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    perfil.write_text(json.dumps({"roto": True}), encoding="utf-8")
    info = bundle.import_bundle(zip_, perfiles, assets, si_existe="pisar")
    assert info["profile"] == perfil
    assert json.loads(perfil.read_text(encoding="utf-8"))["name"] == "Mio"


def test_importing_reports_the_fonts_that_are_missing_here(entorno, tmp_path):
    """La razon numero uno de que un perfil ajeno se vea distinto. No se puede
    arreglar -- las fuentes no se empaquetan, son de Microsoft -- pero se puede
    decir, que es la diferencia entre "se ve raro" y "te falta esta fuente"."""
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
    """Exportar e importar no puede reformatear el perfil: el usuario tiene que
    poder comparar el suyo con el que le vuelve y ver que son el mismo."""
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
    """Exportar dos veces al mismo nombre no puede pisar el bundle anterior sin
    avisar: puede ser el que el usuario ya mando a alguien."""
    perfil, _, assets = entorno
    from vmaxpanel import cli
    monkeypatch.setattr(cli, "assets_dir", lambda: assets)
    salida = tmp_path / "s.vmaxpanel"
    # A proposito con el flag viejo `--exportar` y no con `--export`: los nombres
    # castellanos quedaron como alias para no romper scripts ya escritos, y este
    # es el test que lo demuestra.
    assert cli.main(["--profile", str(perfil), "--exportar", str(salida)]) == 0
    assert cli.main(["--profile", str(perfil), "--exportar", str(salida)]) == 2
    assert "already exists" in capsys.readouterr().out


def test_a_profile_with_crlf_survives_the_roundtrip(entorno, tmp_path):
    """Los perfiles de este repo estan con CRLF -- los escribe loader.save_raw en
    Windows. Leerlos como texto le hace traducir los saltos de linea a LF, asi que
    el perfil que volvia del bundle tenia 60 bytes menos que el original y "el
    mismo" era mentira. Se lee y se escribe en bytes.
    """
    perfil, _, assets = entorno
    # Con indent para que tenga saltos de linea de verdad: el perfil de una sola
    # linea del fixture no ejercita nada.
    crudo = json.dumps(json.loads(perfil.read_text(encoding="utf-8")), indent=1)
    perfil.write_bytes(crudo.replace("\n", "\r\n").encode("utf-8"))
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    info = bundle.import_bundle(zip_, tmp_path / "p2", tmp_path / "a2")
    assert info["profile"].read_bytes() == perfil.read_bytes()
    assert b"\r\n" in info["profile"].read_bytes()


def test_importing_over_a_live_profile_is_atomic(entorno, tmp_path, monkeypatch):
    """El perfil que se pisa puede ser el que el motor esta leyendo AHORA: el
    hot-reload lo relee por hash del contenido, asi que un write_bytes a medias se
    puede leer truncado. `loader.save_raw` usa temporal + reemplazo por esta misma
    razon; importar tenia que hacer lo mismo.

    Se prueba haciendo fallar el reemplazo: si el contenido nuevo se escribiera
    directo sobre el destino, el perfil viejo ya estaria destruido cuando el fallo
    ocurre.
    """
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    viejo = b'{"soy": "el que estaba andando"}'
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

    assert perfil.read_bytes() == viejo, "destruyo el perfil que estaba andando"
    assert not list(perfiles.glob("*.tmp")), "dejo el temporal tirado"


def test_an_asset_is_also_written_atomically(entorno, tmp_path, monkeypatch):
    """Mismo motivo del otro lado: ffmpeg puede estar leyendo el video que se pisa, y
    un archivo a medio escribir es un fondo roto en vez de uno nuevo."""
    perfil, perfiles, assets = entorno
    zip_ = tmp_path / "b.vmaxpanel"
    bundle.export_profile(perfil, zip_, assets_dir=assets)
    (assets / "fondo.mp4").write_bytes(b"el video que se esta reproduciendo")

    import os
    real = os.replace

    def replace_que_falla(a, b, *args, **kw):
        if str(b).endswith("fondo.mp4"):
            raise OSError("disco lleno justo ahora")
        return real(a, b, *args, **kw)

    monkeypatch.setattr(os, "replace", replace_que_falla)
    with pytest.raises(bundle.BundleError):
        bundle.import_bundle(zip_, perfiles, assets, si_existe="pisar")
    assert (assets / "fondo.mp4").read_bytes() == b"el video que se esta reproduciendo"
    assert not list(assets.glob("*.tmp"))
