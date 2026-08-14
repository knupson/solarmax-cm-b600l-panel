"""Smoke tests for the CLI that touch neither the panel nor the sidecar.

`--no-sensors` makes it possible to exercise both the happy path and the broken
profile path without opening the serial port or starting powershell.
"""
import pytest
from PIL import Image

from vmaxpanel import cli


def test_default_profile_path_points_at_an_existing_file():
    path = cli.default_profile_path()
    assert path.name == "vitals.json"
    assert path.is_file()


def test_save_with_no_sensors_writes_a_frame_and_exits_zero(tmp_path):
    out = tmp_path / "out.png"
    rc = cli.main(["--save", str(out), "--no-sensors"])
    assert rc == 0
    im = Image.open(out)
    assert im.size == (320, 1480)


def test_a_missing_profile_exits_with_an_error_instead_of_a_traceback(tmp_path):
    missing = tmp_path / "no-existe.json"
    rc = cli.main(["--profile", str(missing), "--save", str(tmp_path / "out.png"),
                   "--no-sensors"])
    assert rc == 2


def test_log_redirects_stdout_and_stderr_to_the_file(tmp_path):
    """The scheduled task runs under pythonw.exe, which has no console: without
    --log, an engine that dies at logon leaves the panel black and no trace of
    why."""
    log = tmp_path / "panel.log"
    out = tmp_path / "out.png"
    rc = cli.main(["--save", str(out), "--no-sensors", "--log", str(log)])
    assert rc == 0
    assert "saved" in log.read_text(encoding="utf-8")


def test_log_appends_instead_of_truncating(tmp_path):
    """A restart must not erase the evidence of the previous run."""
    log = tmp_path / "panel.log"
    log.write_text("corrida previa\n", encoding="utf-8")
    cli.main(["--save", str(tmp_path / "a.png"), "--no-sensors", "--log", str(log)])
    assert "corrida previa" in log.read_text(encoding="utf-8")


def test_log_captures_the_error_of_a_broken_profile(tmp_path):
    log = tmp_path / "panel.log"
    broken = tmp_path / "roto.json"
    broken.write_text("{no es json", encoding="utf-8")
    rc = cli.main(["--profile", str(broken), "--save", str(tmp_path / "o.png"),
                   "--no-sensors", "--log", str(log)])
    assert rc == 2
    assert "layout:" in log.read_text(encoding="utf-8")


def test_log_captures_a_traceback_from_an_unexpected_crash(tmp_path, monkeypatch):
    """A TypeError inside the render -- the class of failure the final review found
    -- escapes all the way to killing the process. Under pythonw the traceback has
    nowhere to print, so it has to land in the log before stderr is restored."""
    log = tmp_path / "panel.log"

    def boom(*args, **kw):
        raise RuntimeError("explosion de prueba")

    monkeypatch.setattr(cli, "Renderer", boom)
    with pytest.raises(RuntimeError):
        cli.main(["--save", str(tmp_path / "o.png"), "--no-sensors", "--log", str(log)])
    text = log.read_text(encoding="utf-8")
    assert "Traceback" in text and "explosion de prueba" in text


def test_help_offers_the_english_spellings_only(capsys):
    """--help printed {fail,rename,overwrite,fallar,renombrar,pisar} in its usage
    line: the Spanish aliases read as six different options instead of three.
    They stay accepted, they just stop being advertised."""
    import pytest

    from vmaxpanel import cli

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    texto = capsys.readouterr().out
    for castellano in ("fallar", "renombrar", "pisar"):
        assert castellano not in texto, castellano
    assert "overwrite" in texto


def test_the_spanish_spellings_still_work():
    from vmaxpanel import cli
    for grafia, esperado in (("pisar", "pisar"), ("overwrite", "pisar"),
                             ("renombrar", "renombrar"), ("rename", "renombrar")):
        assert cli.SI_EXISTE[cli._si_existe(grafia)] == esperado, grafia
