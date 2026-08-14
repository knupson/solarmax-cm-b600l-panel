"""Watchdog for the test run.

It exists because of a concrete incident: an engine test entered an infinite loop
on a virtual clock -- that is, with no sleep at all -- the run went to the
background, and the process sat eating a whole core for **7.28 hours** until the
user noticed the machine was unresponsive. The `timeout` on the command that
launched it killed the wrapper, not the child python.

A test that hangs has to fail fast and loudly, never be left
girando. Sin dependencias nuevas (`pytest-timeout` haria esto mas prolijo,
but this project gets shared and every dependency is one more installation step
that can fail).

`os._exit()` is deliberate: a hung test cannot be interrupted cleanly from another
thread -- neither an exception nor a `SystemExit` gets a loop without yields out of
its loop -- so the only reliable way out is to end the process. Before that it
dumps every thread's stack, which is what says WHAT
colgo.

The limit can be raised with VMAXPANEL_TEST_TIMEOUT if some test genuinely takes
that long.
"""
import faulthandler
import os
import sys
import threading
from pathlib import Path

import pytest

TIMEOUT = float(os.environ.get("VMAXPANEL_TEST_TIMEOUT", "60"))

# El volcado va a un archivo y no a stderr: pytest captura stderr a nivel de
# descriptor level, so os._exit() takes that buffer with it unwritten and the
# diagnostico -- justamente QUE test se colgo -- se pierde entero.
INFORME = Path(__file__).resolve().parent.parent / "pytest-hang.txt"


@pytest.fixture(autouse=True)
def _watchdog(request):
    if TIMEOUT <= 0:
        yield
        return

    def morir():
        encabezado = (f"TIMEOUT: {request.node.nodeid} went past {TIMEOUT:.0f} s.\n"
                      f"The process is killed so it does not keep spinning.\n"
                      f"Stacks of every thread:\n\n")
        try:
            with open(INFORME, "w", encoding="utf-8") as f:
                f.write(encabezado)
                f.flush()
                faulthandler.dump_traceback(file=f)
        except Exception:
            pass
        # And a warning through raw descriptor 2, which shows up even with -s.
        try:
            os.write(2, (f"\n*** {encabezado}*** detalle en {INFORME}\n")
                     .encode("utf-8", "replace"))
        except Exception:
            pass
        os._exit(3)

    reloj = threading.Timer(TIMEOUT, morir)
    reloj.daemon = True
    reloj.start()
    try:
        yield
    finally:
        reloj.cancel()
