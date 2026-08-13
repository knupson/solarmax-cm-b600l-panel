"""Exportar e importar un perfil como un solo archivo (.vmaxpanel, que es un zip).

**Por que no alcanza con copiar el .json.** Un perfil referencia assets -- el video
o la imagen del fondo, la carpeta de una secuencia -- y nombra fuentes por familia.
Copiado suelto a otra maquina da un panel con el fondo degradado y las fuentes
cambiadas, sin que nadie entienda por que. El bundle lleva el JSON y los assets
juntos, y al importar dice que fuentes no estan aca.

**Las fuentes no se empaquetan y no es un olvido:** Consolas y las Franklin Gothic
son de Microsoft y no se redistribuyen. Se listan en el manifiesto y se avisa al
importar cual falta -- que es la diferencia entre "se ve raro" y "te falta esta
fuente".

**Todo lo que entra de un zip ajeno se trata como hostil.** El proceso que importa
puede estar elevado (la tarea usa HighestAvailable), asi que un miembro con
`..\\..\\` escribiendo donde quiera es un agujero real, no teorico. Se valida el
perfil ANTES de escribir nada, se rechaza cualquier ruta que se escape del destino
y se corta por tamano declarado para no descomprimir una bomba.
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

# Topes: un perfil son kilobytes y el asset mas gordo que tiene sentido es un video
# de unos pocos minutos. Un miembro de 512 MB o un bundle de 2 GB no es un perfil,
# es otra cosa.
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
    """La ruta que el fondo referencia, ya saneada, o None.

    Se pasa por safe_asset_path aunque el perfil venga de esta maquina: exportar
    lee ese archivo, y un src con `..` leeria cualquier cosa del disco para meterla
    en un zip que despues se comparte. Es el mismo chequeo que hace el render, por
    la misma razon.
    """
    bg = raw.get("background") or {}
    if bg.get("type") not in ("image", "sequence", "video"):
        return None
    return schema.safe_asset_path(bg.get("src"))


def export_profile(profile_path, destino, assets_dir) -> dict:
    """Escribe el bundle. Devuelve {assets, faltantes, fonts, profile}.

    Un asset que falta se reporta pero no impide exportar: el motor degrada a color
    plano, asi que el perfil sigue siendo util, y bloquear por eso dejaria al
    usuario sin poder compartir su layout por un archivo que quizas no le importa.
    """
    profile_path = Path(profile_path)
    destino = Path(destino)
    assets_dir = Path(assets_dir)
    try:
        # En bytes y no read_text: leer como texto traduce CRLF a LF -- los
        # perfiles de Windows salen con CRLF -- y el perfil que vuelve del bundle
        # ya no seria byte a byte el que se exporto. json.loads acepta bytes.
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
        # El JSON va tal cual, byte a byte: reescribirlo con json.dump cambiaria el
        # formato y el usuario no podria comparar el suyo con el que le vuelve.
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
    """Rechaza un nombre de miembro que apunte afuera. Zip-slip, primera mitad.

    Lo absoluto, lo que tenga '..' y lo que traiga letra de unidad. Es solo el
    chequeo del texto; el que de verdad cierra la puerta es el de _destino_seguro,
    que compara rutas ya resueltas y no depende de haber enumerado bien todas las
    formas de escribir "subir un nivel".
    """
    limpio = nombre.replace("\\", "/")
    pp = PurePosixPath(limpio)
    if pp.is_absolute() or ".." in pp.parts or (len(limpio) > 1 and limpio[1] == ":"):
        raise BundleError(f"the bundle carries a path that escapes the destination: {nombre!r}")
    return pp


def _destino_seguro(nombre, raiz) -> Path:
    """Resuelve un miembro del zip dentro de `raiz`, o levanta.

    Zip-slip, segunda mitad: despues de resolver, la ruta tiene que quedar debajo
    de la raiz. Este es el chequeo que no depende de reconocer patrones en el
    texto.
    """
    pp = _revisar_nombre(nombre)
    final = (raiz / Path(*pp.parts)).resolve()
    if final != raiz.resolve() and raiz.resolve() not in final.parents:
        raise BundleError(f"the bundle carries a path that escapes the destination: {nombre!r}")
    return final


def _escribir_atomico(destino, datos):
    """Escribe en un temporal al lado y reemplaza.

    Lo que se pisa puede estar EN USO: el perfil lo relee el motor en caliente (por
    hash del contenido, asi que un archivo a medio escribir se puede leer truncado) y
    el asset lo puede estar leyendo un ffmpeg. `loader.save_raw` ya escribia asi por
    esta misma razon; importar tenia que hacer lo mismo. El temporal lleva el pid
    para que dos importaciones a la vez no se pisen el temporal entre ellas.
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
    """Instala el bundle. -> {profile, assets, fuentes_faltantes, manifest}.

    `si_existe`: "fallar" (default), "renombrar" o "pisar". El default no pisa
    porque el layout del usuario es trabajo suyo, y dos personas exportando "apex"
    es lo normal, no la excepcion.
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
        # Se valida ANTES de escribir: un bundle con un perfil roto no puede dejar
        # assets a medio copiar en la carpeta del usuario.
        errores = schema.validate(raw)
        if errores:
            raise BundleError("the bundle's profile is not valid: "
                              + "; ".join(errores))

        try:
            manifiesto = json.loads(z.read(MANIFIESTO)) if MANIFIESTO in nombres else {}
        except ValueError:
            manifiesto = {}

        # Los destinos se resuelven TODOS antes de escribir el primero, por lo
        # mismo: un miembro malicioso en la mitad del zip no puede dejar la mitad
        # buena ya copiada.
        nombre_perfil = manifiesto.get("profile_file") or f"{raw.get('name', 'perfil')}.json"
        destino_perfil = _destino_seguro(Path(nombre_perfil).name, profiles_dir)
        planificados = []
        for nombre in sorted(nombres):
            # El nombre de CADA miembro se revisa, incluso los que no se van a
            # instalar. Un miembro absoluto o con '..' no es un archivo extra de
            # una version futura: es un bundle que intento escribir afuera, y
            # aceptar el resto de un zip asi es confiar en que el unico intento fue
            # el que vi.
            _revisar_nombre(nombre)
            if nombre in (PERFIL, MANIFIESTO) or nombre.endswith("/"):
                continue
            if not nombre.startswith(f"{ASSETS}/"):
                # Un miembro fuera de assets/ no se instala: el bundle define dos
                # lugares y nada mas. Se ignora en silencio en vez de fallar para
                # que un bundle de una version futura con archivos extra siga
                # importando lo que si entiende.
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
        # Byte a byte y no json.dump: exportar e importar no puede reformatear el
        # perfil, o el usuario no puede comparar el suyo con el que le vuelve.
        _escribir_atomico(destino_perfil, crudo)
        instalados = []
        for nombre, destino in planificados:
            destino.parent.mkdir(parents=True, exist_ok=True)
            _escribir_atomico(destino, z.read(nombre))
            instalados.append(str(destino.relative_to(assets_dir)).replace("\\", "/"))

    return {"profile": destino_perfil, "assets": instalados,
            "fuentes_faltantes": _faltan_fuentes(raw), "manifest": manifiesto}


def _faltan_fuentes(raw) -> list:
    """Las familias del perfil que no estan instaladas en ESTA maquina.

    Import time y no export time: la pregunta que importa es "que le va a faltar al
    que lo recibe", y eso solo se puede contestar en la maquina que recibe.
    """
    from .render.fonts import FontResolver
    resolver = FontResolver()
    return [f for f in _familias(raw) if not resolver.is_available(f)]


def describe_bundle(origen) -> dict:
    """El manifiesto de un bundle, sin instalar nada. Para mirar antes de importar."""
    origen = Path(origen)
    try:
        with zipfile.ZipFile(origen) as z:
            _revisar_tamanos(z)
            if MANIFIESTO not in z.namelist():
                raise BundleError(f"{origen.name} has no {MANIFIESTO}")
            return json.loads(z.read(MANIFIESTO))
    except (OSError, zipfile.BadZipFile, ValueError) as e:
        raise BundleError(f"{origen.name} is not a readable bundle: {e}") from e
