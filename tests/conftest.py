"""Perro guardián de la corrida de tests.

Existe por un incidente concreto: un test del engine entro en un bucle
infinito con reloj virtual -- o sea sin ningun sleep --, la corrida se fue a
background y el proceso quedo comiendose un nucleo entero durante **7,28
horas** hasta que el usuario noto que la maquina no respondia. El `timeout`
del comando que la lanzo mato al envoltorio, no al python hijo.

Un test que se cuelga tiene que fallar rapido y ruidoso, nunca quedar
girando. Sin dependencias nuevas (`pytest-timeout` haria esto mas prolijo,
pero este proyecto se reparte y cada dependencia es una instalacion mas que
puede fallar).

`os._exit()` es a proposito: un test colgado no se puede interrumpir de forma
ordenada desde otro hilo -- ni una excepcion ni un `SystemExit` sacan a un
bucle sin yields --, asi que la unica salida confiable es terminar el proceso.
Antes de eso volca el stack de todos los hilos, que es lo que dice QUE se
colgo.

El limite se puede subir con VMAXPANEL_TEST_TIMEOUT si algun test tarda de
verdad. Hoy la suite entera corre en ~9 s y el test mas lento no llega a 3 s.
"""
import faulthandler
import os
import sys
import threading
from pathlib import Path

import pytest

TIMEOUT = float(os.environ.get("VMAXPANEL_TEST_TIMEOUT", "60"))

# El volcado va a un archivo y no a stderr: pytest captura stderr a nivel de
# descriptor, asi que os._exit() se lleva ese buffer sin escribir nada y el
# diagnostico -- justamente QUE test se colgo -- se pierde entero.
INFORME = Path(__file__).resolve().parent.parent / "pytest-hang.txt"


@pytest.fixture(autouse=True)
def _watchdog(request):
    if TIMEOUT <= 0:
        yield
        return

    def morir():
        encabezado = (f"TIMEOUT: {request.node.nodeid} paso de {TIMEOUT:.0f} s.\n"
                      f"Se mata el proceso para que no quede girando.\n"
                      f"Stack de todos los hilos:\n\n")
        try:
            with open(INFORME, "w", encoding="utf-8") as f:
                f.write(encabezado)
                f.flush()
                faulthandler.dump_traceback(file=f)
        except Exception:
            pass
        # Y un aviso por el descriptor 2 crudo, que aparece igual con -s.
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
