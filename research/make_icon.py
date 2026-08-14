"""Genera vmaxpanel/assets/vmaxpanel.ico.

Arte propio, no del vendor. El icono tiene que leerse a 16 px en la bandeja,
asi que no hay detalle fino: un panel vertical oscuro con el bloque grande de
un valor y una barra de progreso debajo, que es exactamente la forma que tiene
el layout real. A 16 px se distingue como "algo con una barra" en vez de como
un cuadrado gris.

Se corre a mano cuando hay que cambiar el arte: el .ico va commiteado.

    python research/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

DESTINO = Path(__file__).resolve().parent.parent / "vmaxpanel/assets/vmaxpanel.ico"

FONDO = (11, 15, 23, 255)          # #0B0F17, el fondo del perfil
BORDE = (57, 135, 229, 255)        # #3987E5, el azul de las barras
VALOR = (255, 255, 255, 255)
PISTA = (36, 40, 52, 255)          # #242834, el track de las barras
TAMANOS = [256, 128, 64, 48, 32, 24, 16]


def dibujar(lado: int) -> Image.Image:
    """El icono a un tamano dado. Todo en proporciones de `lado` para que las
    formas caigan en los mismos lugares en cada resolucion."""
    im = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    g = ImageDraw.Draw(im)
    u = lado / 16.0                                  # unidad: 1/16 del lado

    # El panel: alto y angosto, como el de verdad (320x1480 es 1:4.6).
    ancho = round(7 * u)
    alto = round(13 * u)
    x0 = (lado - ancho) // 2
    y0 = (lado - alto) // 2
    radio = max(1, round(1.5 * u))
    g.rounded_rectangle([x0, y0, x0 + ancho - 1, y0 + alto - 1], radius=radio,
                        fill=FONDO, outline=BORDE, width=max(1, round(u)))

    # El bloque del valor grande, arriba.
    m = max(1, round(1.5 * u))
    g.rectangle([x0 + m, y0 + round(2.2 * u),
                 x0 + ancho - 1 - m, y0 + round(6 * u)], fill=VALOR)

    # Dos barras: la pista completa y el relleno a ~60%.
    for i, arriba in enumerate((7.6, 10.2)):
        by0 = y0 + round(arriba * u)
        by1 = by0 + max(1, round(1.4 * u))
        g.rectangle([x0 + m, by0, x0 + ancho - 1 - m, by1], fill=PISTA)
        lleno = round((ancho - 1 - 2 * m) * (0.62 if i == 0 else 0.35))
        g.rectangle([x0 + m, by0, x0 + m + lleno, by1], fill=BORDE)
    return im


def main():
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    # El .ico guarda todas las resoluciones juntas; Windows elige la que
    # necesita. Se dibuja cada una por separado en vez de escalar la grande:
    # a 16 px un downscale de 256 deja las barras en un gris ilegible.
    capas = [dibujar(t) for t in TAMANOS]
    capas[0].save(DESTINO, format="ICO",
                  sizes=[(t, t) for t in TAMANOS], append_images=capas[1:])
    print(f"guardado {DESTINO} ({DESTINO.stat().st_size} bytes, "
          f"{len(TAMANOS)} resoluciones)")


if __name__ == "__main__":
    main()
