"""Spike de throughput del panel: cuantos fps traga, y quien es el cuello.

Decide todo el diseno de fase 2 (fondos animados). Mide tres cosas por
separado, porque son tres limites distintos y solo el mas bajo importa:

  1. render   -- cuanto tarda Renderer.frame() en armar el cuadro (CPU)
  2. encode   -- cuanto tarda to_jpeg() y cuantos bytes salen
  3. transporte -- cuanto tarda el write serial de esos bytes

No toca sensores: usa una muestra fija, porque medir el sidecar seria medir
otra cosa. Con `--dry` no abre el panel y mide solo 1 y 2.

    python research/spike_throughput.py --dry
    python research/spike_throughput.py --frames 60
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vmaxpanel.layout import loader                                  # noqa: E402
from vmaxpanel.render.renderer import Renderer, to_jpeg               # noqa: E402
from vmaxpanel.transport.panel_link import PanelLink                  # noqa: E402

PERFIL = Path(__file__).resolve().parent.parent / "vmaxpanel/profiles/vitals.json"

MUESTRA = {
    "clock.time": "14:32", "clock.date": "LUN 11 AGO",
    "cpu.name": "INTEL CORE i5-12400F", "cpu.load": 55.5, "cpu.temp": 48.0,
    "cpu.clock": 4080.0, "cpu.vcore": 1.05, "cpu.vrm_temp": 41.0,
    "gpu.name": "AMD RADEON RX 6800 XT", "gpu.load": 23.0, "gpu.temp": 51.0,
    "gpu.hotspot": 68.0, "gpu.clock": 1850.0, "gpu.power": 84.0, "gpu.vram": 37.0,
    "mem.load": 42.3, "mem.used": 13.5, "mem.speed": 5600.0,
    "net.down": 1258291.0, "net.up": 40960.0,
    "disk.temp.0": 34.0, "disk.temp.1": 40.0, "disk.temp.2": 41.0,
}


def ms(xs):
    return f"{statistics.mean(xs) * 1000:.1f} ms (p95 {sorted(xs)[int(len(xs) * .95)] * 1000:.1f})"


def medir(frames, calidades, dry, port=None):
    layout = loader.load(PERFIL)
    link = None
    if not dry:
        link = PanelLink.autodetect(port)
        sn = link.open()
        print(f"panel abierto, sn {sn!r}, geometria {link.geometry}")
        link.set_brightness(layout.panel.brightness)

    renderer = Renderer(layout, panel_size=link.geometry if link else None)

    # El primer frame paga la carga de fuentes y el armado del fondo: se
    # descarta para no contaminar la media.
    renderer.frame(MUESTRA)

    for q in calidades:
        t_render, t_encode, t_write, tamanos = [], [], [], []
        t0 = time.perf_counter()
        for _ in range(frames):
            a = time.perf_counter()
            img = renderer.frame(MUESTRA)
            b = time.perf_counter()
            data = to_jpeg(img, layout.panel.rotate, q)
            c = time.perf_counter()
            if link is not None:
                link.send_frame(data)
            d = time.perf_counter()
            t_render.append(b - a)
            t_encode.append(c - b)
            t_write.append(d - c)
            tamanos.append(len(data))
        total = time.perf_counter() - t0

        kb = statistics.mean(tamanos) / 1024
        print(f"\ncalidad {q}: {kb:.1f} KB/frame")
        print(f"  render     {ms(t_render)}")
        print(f"  encode     {ms(t_encode)}")
        if link is not None:
            print(f"  write      {ms(t_write)}")
            bps = sum(tamanos) / sum(t_write) if sum(t_write) else 0
            print(f"  throughput {bps / 1048576:.2f} MB/s de escritura")
        print(f"  => {frames / total:.1f} fps sostenidos "
              f"({total / frames * 1000:.1f} ms por frame)")

        # Lo de arriba mide cuan rapido el HOST entrega bytes al driver CDC,
        # no cuan rapido el panel los dibuja. Si el panel consume menos, su
        # buffer se llena y el write empieza a bloquear: el ritmo por tramos
        # es lo que delata el limite real del panel en vez del del bus.
        if link is not None and len(t_write) >= 25:
            tramo = len(t_write) // 5
            ritmos = []
            for i in range(5):
                trozo = t_write[i * tramo:(i + 1) * tramo]
                dur = sum(trozo)
                ritmos.append(tramo / dur if dur else float("inf"))
            print("     write fps por quinto: "
                  + " · ".join(f"{r:.0f}" for r in ritmos))

    if link is not None:
        link.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--dry", action="store_true", help="no abre el panel")
    ap.add_argument("--port")
    ap.add_argument("--quality", type=int, nargs="*", default=[82, 60, 40])
    a = ap.parse_args()
    medir(a.frames, a.quality, a.dry, a.port)


if __name__ == "__main__":
    main()
