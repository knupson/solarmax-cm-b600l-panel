"""Exporting and importing a profile as a single file (.vmaxpanel, which is a zip).

**Why copying the .json is not enough.** A profile references assets -- the video
or image of the background, a sequence's folder -- and names fonts by family.
Copied on its own to another machine it gives a panel with a degraded background
and different fonts, with nobody understanding why. The bundle carries the JSON
and the assets together, and on import it says which fonts are absent here.

**Fonts are not packaged, and that is not an oversight:** Consolas and the Franklin
Gothic family belong to Microsoft and are not redistributable. They are listed in
the manifest and on import you are told which one is missing -- which is the
difference between "it looks wrong" and "you are missing this
fuente".

**Everything coming out of somebody else's zip is treated as hostile.** The
importing process can be elevated (the scheduled task uses HighestAvailable), so a
member with `..\\..\\` writing wherever it likes is a real hole, not a theoretical
one. The profile is validated BEFORE anything is written, any path escaping the
destination is rejected, and extraction is bounded by declared size so a zip bomb
cannot run away.
"""
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .layout import loader, schema

FORMATO = 1
EXT = ".vmaxpanel"
PERFIL = "perfil.json"
MANIFIESTO = "bundle.json"
ASSETS = "assets"

# Caps: a profile is kilobytes and the fattest asset that makes sense is a video
# de unos pocos minutos. Un miembro de 512 MB o un bundle de 2 GB no es un perfil,
# is something else.
MAX_MIEMBRO = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024


class BundleError(Exception):
    pass


# --- exportar ---


def _familias(raw) -> list:
    fuentes = raw.get("fonts") or {}
    vistas = {}
    for f in fuentes.values():
        fam = (f or {}).get("family")
        if isinstance(fam, str) and fam.strip():
            vistas.setdefault(fam.lower(), fam.strip())
    return [vistas[k] for k in sorted(vistas)]


def _asset_del_fondo(raw) -> str | None:
    """The path the background references, already sanitised, or None.

    It goes through safe_asset_path even when the profile comes from this machine:
    exporting reads that file, and a src with `..` would read anything off the disk
    to put it in a zip that then gets shared. It is the same check the renderer
    does, for the same reason.
    """
    bg = raw.get("background") or {}
    if bg.get("type") not in ("image", "sequence", "video"):
        return None
    return schema.safe_asset_path(bg.get("src"))


def export_profile(profile_path, destino, assets_dir) -> dict:
    """Writes the bundle. Returns {assets, faltantes, fonts, profile}.

    A missing asset is reported but does not stop the export: the engine degrades
    to a flat colour, so the profile is still useful, and blocking over it would
    leave the user unable to share their layout because of a file they may not care
    about.
    """
    profile_path = Path(profile_path)
    destino = Path(destino)
    assets_dir = Path(assets_dir)
    try:
        # In bytes and not read_text: reading as text translates CRLF to LF --
        # Windows profiles come out with CRLF -- and the profile coming back out of
        # the bundle would no longer be byte for byte the one exported. json.loads
        # accepts bytes.
        crudo = profile_path.read_bytes()
        raw = json.loads(crudo)
    except (OSError, ValueError) as e:
        raise BundleError(f"could not read the profile {profile_path.name}: {e}") from e
    errores = schema.validate(raw)
    if errores:
        raise BundleError("the profile is not valid, not exporting: "
                          + "; ".join(errores))

    incluidos, faltantes = [], []
    referencia = _asset_del_fondo(raw)
    origen = (assets_dir / referencia) if referencia else None

    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        # The JSON goes in as-is, byte for byte: rewriting it with json.dump would
        # change the formatting and the user could not compare theirs with the one
        # that comes back.
        z.writestr(PERFIL, crudo)
        if origen is not None:
            if origen.is_dir():
                archivos = sorted(p for p in origen.rglob("*") if p.is_file())
                if archivos:
                    for p in archivos:
                        rel = p.relative_to(assets_dir).as_posix()
                        z.write(p, f"{ASSETS}/{rel}")
                    incluidos.append(referencia)
                else:
                    faltantes.append(referencia)
            elif origen.is_file():
                z.write(origen, f"{ASSETS}/{referencia}")
                incluidos.append(referencia)
            else:
                faltantes.append(referencia)
        manifiesto = {
            "format": FORMATO,
            "name": raw.get("name"),
            "designed_for": raw.get("designed_for"),
            "panel": raw.get("panel"),
            "fonts": _familias(raw),
            "assets": incluidos,
            "widgets": len(raw.get("widgets") or []),
            "exported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "profile_file": profile_path.name,
        }
        z.writestr(MANIFIESTO, json.dumps(manifiesto, indent=1, ensure_ascii=False))

    return {"assets": incluidos, "faltantes": faltantes,
            "fonts": manifiesto["fonts"], "profile": profile_path,
            "bundle": destino}


# --- importar ---


def _revisar_nombre(nombre):
    """Rejects a member name pointing outside. Zip-slip, first half.

    Anything absolute, anything with '..' and anything carrying a drive letter. It
    is only the textual check; the one that really closes the door is in
    _destino_seguro, which compares already-resolved paths and does not depend on
    having correctly enumerated every
    formas de escribir "subir un nivel".
    """
    limpio = nombre.replace("\\", "/")
    pp = PurePosixPath(limpio)
    if pp.is_absolute() or ".." in pp.parts or (len(limpio) > 1 and limpio[1] == ":"):
        raise BundleError(f"the bundle carries a path that escapes the destination: {nombre!r}")
    return pp


def _destino_seguro(nombre, raiz) -> Path:
    """Resolves a zip member inside `raiz`, or raises.

    Zip-slip, second half: after resolving, the path has to land below the root.
    This is the check that does not depend on recognising patterns in the
    texto.
    """
    pp = _revisar_nombre(nombre)
    final = (raiz / Path(*pp.parts)).resolve()
    if final != raiz.resolve() and raiz.resolve() not in final.parents:
        raise BundleError(f"the bundle carries a path that escapes the destination: {nombre!r}")
    return final


def _escribir_atomico(destino, datos):
    """Escribe en un temporal al lado y reemplaza.

    What gets overwritten may be IN USE: the engine re-reads the profile live (by
    content hash, so a half-written file can be read truncated) and an ffmpeg may be
    reading the asset. `loader.save_raw` already wrote this way for the same reason;
    importing had to do the same. The temp file carries the pid so two simultaneous
    imports do not clobber each other.
    """
    tmp = destino.with_name(f"{destino.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(datos)
        os.replace(tmp, destino)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise BundleError(f"could not write {destino.name}: {e}") from e


def _revisar_tamanos(z):
    total = 0
    for info in z.infolist():
        if info.file_size > MAX_MIEMBRO:
            raise BundleError(
                f"{info.filename!r} is too large "
                f"({info.file_size / 1e6:.0f} MB): not importing")
        total += info.file_size
    if total > MAX_TOTAL:
        raise BundleError(f"the bundle expands to {total / 1e9:.1f} GB: not importing")


def _nombre_libre(carpeta, nombre) -> Path:
    base, ext = Path(nombre).stem, Path(nombre).suffix
    for i in range(2, 1000):
        candidato = carpeta / f"{base}-{i}{ext}"
        if not candidato.exists():
            return candidato
    raise BundleError(f"could not find a free name for {nombre}")


def import_bundle(origen, profiles_dir, assets_dir, si_existe="fallar") -> dict:
    """Installs the bundle. -> {profile, assets, fuentes_faltantes, manifest}.

    `si_existe`: "fallar" (default), "renombrar" o "pisar". El default no pisa
    because the user's layout is their own work, and two people exporting "apex" is
    the normal case, not the exception.
    """
    origen = Path(origen)
    profiles_dir = Path(profiles_dir)
    assets_dir = Path(assets_dir)
    if si_existe not in ("fallar", "renombrar", "pisar"):
        raise BundleError(f"invalid si_existe: {si_existe!r}")

    try:
        z = zipfile.ZipFile(origen)
    except (OSError, zipfile.BadZipFile) as e:
        raise BundleError(f"{origen.name} is not a readable bundle: {e}") from e

    with z:
        _revisar_tamanos(z)
        nombres = set(z.namelist())
        if PERFIL not in nombres:
            raise BundleError(f"{origen.name} has no {PERFIL}: it is not a vmaxpanel bundle")
        crudo = z.read(PERFIL)
        try:
            raw = json.loads(crudo)
        except ValueError as e:
            raise BundleError(f"the bundle's profile is not valid JSON: {e}") from e
        # Validated BEFORE writing: a bundle with a broken profile must not leave
        # assets half-copied in the user's folder.
        errores = schema.validate(raw)
        if errores:
            raise BundleError("the bundle's profile is not valid: "
                              + "; ".join(errores))

        try:
            manifiesto = json.loads(z.read(MANIFIESTO)) if MANIFIESTO in nombres else {}
        except ValueError:
            manifiesto = {}

        # ALL destinations are resolved before the first one is written, for the
        # same reason: a malicious member halfway through the zip must not leave the
        # good half
        # buena ya copiada.
        nombre_perfil = manifiesto.get("profile_file") or f"{raw.get('name', 'perfil')}.json"
        destino_perfil = _destino_seguro(Path(nombre_perfil).name, profiles_dir)
        planificados = []
        for nombre in sorted(nombres):
            # EVERY member name is checked, including the ones that will not be
            # installed. An absolute member or one with '..' is not an extra file
            # from a future version: it is a bundle that tried to write outside, and
            # accepting the rest of such a zip is trusting that the only attempt was
            # the one I happened to see.
            _revisar_nombre(nombre)
            if nombre in (PERFIL, MANIFIESTO) or nombre.endswith("/"):
                continue
            if not nombre.startswith(f"{ASSETS}/"):
                # A member outside assets/ is not installed: the bundle defines two
                # places and no more. It is ignored silently rather than failing, so
                # that a bundle from a future version with extra files still imports
                # whatever this version does understand.
                continue
            relativo = nombre[len(ASSETS) + 1:]
            planificados.append((nombre, _destino_seguro(relativo, assets_dir)))

        if destino_perfil.exists():
            if si_existe == "fallar":
                raise BundleError(f"{destino_perfil.name} already exists in "
                                  f"{profiles_dir}: import with 'rename' or "
                                  f"'overwrite' if that is what you want")
            if si_existe == "renombrar":
                destino_perfil = _nombre_libre(profiles_dir, destino_perfil.name)

        profiles_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)
        # Byte for byte and not json.dump: exporting and importing must not
        # reformat the profile, or the user cannot compare theirs with the one that
        # comes back.
        _escribir_atomico(destino_perfil, crudo)
        instalados = []
        for nombre, destino in planificados:
            destino.parent.mkdir(parents=True, exist_ok=True)
            _escribir_atomico(destino, z.read(nombre))
            instalados.append(str(destino.relative_to(assets_dir)).replace("\\", "/"))

    return {"profile": destino_perfil, "assets": instalados,
            "fuentes_faltantes": _faltan_fuentes(raw), "manifest": manifiesto}


def _faltan_fuentes(raw) -> list:
    """The profile's families that are not installed on THIS machine.

    At import time and not at export time: the question that matters is "what will
    the recipient be missing", and that can only be answered on the receiving
    machine.
    """
    from .render.fonts import FontResolver
    resolver = FontResolver()
    return [f for f in _familias(raw) if not resolver.is_available(f)]


def describe_bundle(origen) -> dict:
    """A bundle's manifest, without installing anything. To look before importing."""
    origen = Path(origen)
    try:
        with zipfile.ZipFile(origen) as z:
            _revisar_tamanos(z)
            if MANIFIESTO not in z.namelist():
                raise BundleError(f"{origen.name} has no {MANIFIESTO}")
            return json.loads(z.read(MANIFIESTO))
    except (OSError, zipfile.BadZipFile, ValueError) as e:
        raise BundleError(f"{origen.name} is not a readable bundle: {e}") from e
