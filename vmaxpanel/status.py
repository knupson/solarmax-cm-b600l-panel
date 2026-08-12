"""El estado del panel, publicado a un archivo para que se pueda leer desde afuera.

**El agujero que cierra.** La bandeja tiene el estado en su menu, pero desde una
consola -- o desde un script, o desde otra sesion -- lo unico observable era el log
y el CPU del proceso, y de ahi a "esta dibujando" hay un salto de fe. Paso tres
veces en un dia: verificar que el panel andaba midiendo el CPU de un pythonw.

**Un archivo y no un socket ni una tuberia con nombre.** El lector no necesita
hablar con el proceso, solo saber que ve: un archivo lo hace sin abrir un puerto,
sin permisos raros, sin que un lector colgado afecte al motor y sin que el motor
tenga que atender a nadie. El costo es que el dato es de hace unos segundos, y por
eso la antiguedad se reporta siempre.

**Se escribe con reemplazo atomico** (temporal + os.replace): un lector que llega
en el medio ve el archivo viejo entero, nunca medio archivo nuevo.
"""
import json
import os
import time
from pathlib import Path

PERIODO = 5.0            # cada cuanto publica el motor
VIEJO = 30.0             # a partir de aca se avisa que el dato es rancio


class StatusFile:
    """El archivo de estado. Nunca levanta: esto es diagnostico, no funcionalidad.

    Un disco lleno o un permiso denegado no puede matar el hilo del motor ni la
    bandeja -- seria un colmo que el mecanismo para saber si el panel anda fuera el
    que lo apaga.
    """

    def __init__(self, path, clock=None):
        self.path = Path(path)
        self._clock = clock or time.time

    def write(self, estado) -> bool:
        datos = dict(estado)
        datos["ts"] = self._clock()
        datos["pid"] = os.getpid()
        tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(datos, ensure_ascii=False, default=str),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def read(self):
        """El ultimo estado publicado, o None si no hay o no se entiende."""
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def _vivo(pid) -> bool:
    """Si ese pid sigue existiendo y parece un Python.

    Lo segundo importa: los pid se reciclan, y un archivo de estado viejo cuyo pid
    ahora es un notepad reportaria "vivo" con datos de otra corrida.
    """
    try:
        import psutil
        return "python" in psutil.Process(int(pid)).name().lower()
    except Exception:
        return False


def describe(estado, ahora=None, vivo=None) -> str:
    """El estado en texto, para imprimir. Nunca levanta."""
    if not estado:
        return ("el panel no esta corriendo (no hay archivo de estado). "
                "Levantalo con la tarea PanelVitals o con "
                "'python -m vmaxpanel.tray'.")
    ahora = ahora if ahora is not None else time.time()
    vivo = vivo or _vivo
    edad = max(0.0, ahora - float(estado.get("ts") or 0))
    pid = estado.get("pid")

    lineas = []
    if not vivo(pid):
        # Primero y explicito: con el proceso muerto todo lo que sigue es historia,
        # y mostrar "dibujando, 12000 frames" sin decir esto seria mentir.
        lineas.append(f"el proceso {pid} que escribio esto YA NO EXISTE: "
                      f"lo de abajo es la ultima foto antes de que se fuera.")
    if estado.get("paused"):
        cabeza = "EN PAUSA (el puerto esta libre)"
    elif estado.get("running"):
        cabeza = "dibujando"
    else:
        cabeza = "DETENIDO"
    lineas.append(f"{cabeza} — perfil {estado.get('profile') or '?'}, "
                  f"panel {estado.get('panel') or '?'}, "
                  f"{estado.get('frames') or 0} frames, "
                  f"{estado.get('fps') or '?'} fps")
    lineas.append(f"publicado hace {edad:.0f} s (pid {pid})")
    if edad > VIEJO and estado.get("running"):
        # Un proceso puede estar vivo y no publicar: un motor trabado en una
        # escritura al puerto sigue existiendo. La antiguedad es la unica senal, y
        # decirlo es la diferencia entre un numero viejo y un diagnostico.
        lineas.append(f"OJO: deberia publicar cada {PERIODO:.0f} s. Con {edad:.0f} s "
                      f"de atraso, el motor puede estar colgado.")
    for p in (estado.get("problems") or []):
        lineas.append(f"  - {p}")
    return "\n".join(lineas)
