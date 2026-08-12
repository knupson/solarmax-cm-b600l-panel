"""Redireccion de salida a un archivo de log.

La comparten las dos entradas graficas del proyecto (`cli` y `tray`) porque
las dos pueden correr bajo `pythonw.exe`, que no tiene consola: sin esto, un
proceso que muere al logon deja el panel negro y ningun rastro de por que.
"""
import sys
import traceback


class Tee:
    """Escribe en el archivo de log y, si hay consola, tambien en ella.

    Con pythonw, sys.stdout/sys.stderr pueden ser None -- de ahi el chequeo de
    `stream` antes de escribir.

    flush en cada linea a proposito: lo que se quiere leer es justamente lo
    ultimo que se escribio antes de morir, y un buffer sin vaciar se lo lleva
    puesto.
    """

    def __init__(self, fh, stream):
        self._fh, self._stream = fh, stream

    def write(self, s):
        self._fh.write(s)
        self._fh.flush()
        if self._stream is not None:
            try:
                self._stream.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        self._fh.flush()
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass


def run_with_log(log_path, fn):
    """Corre `fn()` con stdout/stderr duplicados a `log_path`.

    Con `log_path=None` no toca nada y llama a `fn()` directo, para que la
    ruta de linea de comandos siga imprimiendo en la terminal como siempre.
    """
    if log_path is None:
        return fn()

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8", errors="replace") as fh:
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = Tee(fh, saved_out), Tee(fh, saved_err)
        try:
            return fn()
        except BaseException:
            # El traceback lo imprime el interprete DESPUES de que la funcion
            # termina, o sea despues de que el finally restaure stderr y cierre
            # el archivo: para entonces ya no hay donde escribirlo. Se emite
            # aca, mientras stderr todavia es el Tee.
            traceback.print_exc(file=sys.stderr)
            raise
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
