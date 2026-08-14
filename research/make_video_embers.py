"""Genera vmaxpanel/assets/embers.mp4: brasas naranjas subiendo, en loop perfecto.

Arte propio, generado: el punto es tener un fondo de video redistribuible con el
que probar el decoder, sin meter nada de nadie en el repo.

**El loop cierra exacto** y eso no es cosmetico: `-stream_loop -1` reinicia el
archivo sin transicion, asi que si el ultimo cuadro no empalma con el primero se
ve un tiron cada D segundos, para siempre. Se logra haciendo que todo lo que se
mueve tenga un periodo que divide D: cada brasa recorre el alto del panel un
numero entero de veces y su parpadeo hace un numero entero de ciclos.

Por que un video y no el fondo `procedural`: procedural es un degradado
desplazandose, nada mas. Esto tiene decenas de elementos con profundidad, glow y
parpadeo independiente -- calcularlo por cuadro en Python costaria mas que el
presupuesto de 16,7 ms a 60 fps, y precalculado en video sale gratis.

Uso:
    python research/make_video_embers.py            # 320x1480, 8 s, 30 fps
    python research/make_video_embers.py --duracion 12 --fps 24
"""
import argparse
import math
import random
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "vmaxpanel" / "assets" / "embers.mp4"

ANCHO, ALTO = 320, 1480
FONDO_ARRIBA = (6, 7, 10)
FONDO_ABAJO = (26, 12, 4)          # el naranja del perfil, casi apagado
NARANJA = (255, 77, 0)             # #FF4D00, el color del perfil Apex
SEMILLA = 20260812                 # reproducible: el mismo video en cada corrida


def gradiente() -> Image.Image:
    """El fondo quieto. Vertical y no plano para que el panel tenga peso abajo,
    de donde salen las brasas."""
    img = Image.new("RGB", (ANCHO, ALTO))
    d = ImageDraw.Draw(img)
    for y in range(ALTO):
        k = y / (ALTO - 1)
        # cuadratica y no lineal: el calor se concentra en el ultimo tercio en vez
        # de lavar todo el panel con naranja.
        k = k * k
        d.line([(0, y), (ANCHO, y)],
               fill=tuple(round(a + (b - a) * k)
                          for a, b in zip(FONDO_ARRIBA, FONDO_ABAJO)))
    return _granular(img)


def _granular(img) -> Image.Image:
    """Grano fijo de +-2 niveles sobre el gradiente.

    Es el arreglo del banding: un degradado oscuro de 8 bits pasado por h264 en
    yuv420p sale con escalones horizontales visibles -- el codec no tiene
    resolucion para distinguir un nivel de diferencia y colapsa franjas enteras al
    mismo valor. Un grano leve rompe esas franjas y el ojo lo integra como
    textura. Fijo y no por cuadro: un grano que cambia es ruido que el codec tiene
    que codificar 240 veces, y se ve como una tele mal sintonizada.
    """
    rnd = random.Random(SEMILLA + 1)
    grano = Image.new("RGB", img.size)
    grano.putdata([(rnd.randint(0, 4),) * 3 for _ in range(img.width * img.height)])
    # -2 via offset para que el grano no aclare el fondo en promedio.
    return ImageChops.add(img, grano, offset=-2)


def brasas(cuantas=110):
    """Cada brasa con su fase; nada aleatorio por cuadro.

    `vueltas` y `ciclos` son enteros a proposito: son las dos cosas que hacen que
    el ultimo cuadro empalme con el primero.
    """
    rnd = random.Random(SEMILLA)
    out = []
    for _ in range(cuantas):
        out.append({
            "x": rnd.uniform(-10, ANCHO + 10),
            "fase": rnd.random(),                    # donde arranca su recorrido
            "vueltas": rnd.choice((1, 1, 1, 2)),     # una sube el doble de rapido
            "radio": rnd.uniform(1.6, 7.0),
            "brillo": rnd.uniform(0.45, 1.0),
            "ciclos": rnd.choice((2, 3, 4, 6)),      # parpadeo
            "fase_parpadeo": rnd.random(),
            "deriva": rnd.uniform(-14, 14),          # se van de costado al subir
        })
    # Las grandes al final: se dibujan encima de las chicas, que es lo que da la
    # sensacion de profundidad.
    return sorted(out, key=lambda b: b["radio"])


def cuadro(base, brasas_, t, duracion) -> Image.Image:
    """Un cuadro: el gradiente + el glow de las brasas, sumado."""
    mascara = Image.new("L", (ANCHO, ALTO), 0)
    d = ImageDraw.Draw(mascara)
    avance = t / duracion
    for b in brasas_:
        # % 1.0 -> la brasa reaparece abajo al salir por arriba, y en t=duracion
        # esta exactamente donde estaba en t=0.
        p = (b["fase"] + avance * b["vueltas"]) % 1.0
        y = ALTO - p * (ALTO + 40) + 20
        x = b["x"] + math.sin(2 * math.pi * (p + b["fase_parpadeo"])) * b["deriva"]
        parpadeo = 0.55 + 0.45 * math.sin(
            2 * math.pi * (avance * b["ciclos"] + b["fase_parpadeo"]))
        # Se apagan al subir: una brasa que llega arriba igual de brillante que
        # abajo se lee como una particula, no como algo que se enfria.
        vida = max(0.0, 1.0 - p * 0.85)
        v = round(255 * b["brillo"] * parpadeo * vida)
        if v <= 0:
            continue
        r = b["radio"]
        d.ellipse([x - r, y - r, x + r, y + r], fill=v)

    # Dos pasadas: el halo ancho y flojo, y el nucleo angosto encima. Sin el
    # nucleo las brasas son manchas sin centro; sin el halo son puntos duros.
    fuera = base
    # El halo pesa casi tanto como el nucleo: el panel esta lleno de texto y
    # bloques, asi que un fondo timido queda tapado y se lee como "no hay fondo"
    # -- que es exactamente lo que paso con la primera version del procedural.
    for radio, peso in ((7, 0.9), (1, 1.0)):
        fuera = _sumar(fuera, mascara.filter(ImageFilter.GaussianBlur(radio)), peso)
    return fuera


def _sumar(fondo, mascara, peso) -> Image.Image:
    """fondo + naranja*mascara*peso, saturando.

    Suma y no mezcla: una brasa ILUMINA lo que hay debajo, no lo reemplaza. Con
    `composite` el fondo desaparece donde la mascara es fuerte y las brasas se ven
    pegadas encima, como stickers.
    """
    aporte = Image.new("RGB", fondo.size, (0, 0, 0))
    aporte.paste(Image.new("RGB", fondo.size, NARANJA), (0, 0),
                 mascara.point(lambda v: round(v * peso)))
    return ImageChops.add(fondo, aporte)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duracion", type=float, default=8.0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--salida", type=Path, default=DESTINO)
    a = ap.parse_args(argv)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("falta ffmpeg en el PATH", file=sys.stderr)
        return 2

    n = int(round(a.duracion * a.fps))
    base = gradiente()
    bs = brasas()
    a.salida.parent.mkdir(parents=True, exist_ok=True)

    # Los cuadros van por stdin en rgb24 crudo, sin PNGs intermedios: son 1,4 MB
    # cada uno y no hay razon para que toquen el disco.
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ANCHO}x{ALTO}",
           "-r", f"{a.fps:g}", "-i", "-",
           # crf bajo a proposito: el material es casi negro y ahi el codec se
           # come los degradados. 17 sobre 320x1480 sigue dando un archivo chico.
           "-c:v", "libx264", "-preset", "slow", "-crf", "17",
           # yuv420p: es lo que reproduce cualquier cosa. Un mp4 en yuv444 anda en
           # ffmpeg y en poco mas.
           "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           str(a.salida)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        t = i / a.fps
        p.stdin.write(cuadro(base, bs, t, a.duracion).tobytes())
        if i % 30 == 0:
            print(f"  cuadro {i}/{n}")
    p.stdin.close()
    if p.wait() != 0:
        print("ffmpeg fallo", file=sys.stderr)
        return 1
    kb = a.salida.stat().st_size / 1024
    print(f"listo: {a.salida} ({kb:.0f} KB, {n} cuadros, loop de {a.duracion:g} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
