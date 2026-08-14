"""Redirecting output to a log file.

Shared by the project's two entry points (`cli` and `tray`) because both can run
under `pythonw.exe`, which has no console: without this, a process that dies at
logon leaves the panel black and no trace of why.
"""
import sys
import traceback


class Tee:
    """Writes to the log file and, if there is a console, to that as well.

    Under pythonw, sys.stdout/sys.stderr can be None -- hence the `stream` check
    before writing.

    Flushing on every line on purpose: what you want to read is precisely the last
    thing written before dying, and an unflushed buffer takes exactly that with
    it.
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
    """Runs `fn()` with stdout/stderr duplicated into `log_path`.

    With `log_path=None` it touches nothing and calls `fn()` directly, so the
    command-line path keeps printing to the terminal as usual.
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
            # The interpreter prints the traceback AFTER the function returns,
            # which is to say after the finally restores stderr and closes the
            # file: by then there is nowhere left to write it. It is emitted here,
            # while stderr is still the Tee.
            traceback.print_exc(file=sys.stderr)
            raise
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
