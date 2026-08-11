"""Driver propio para el panel Solarmax CM-B600L (320x1480, HL-VMAX, COM3).

Reemplaza a "LCD Control", cuyo % de CPU es `% Processor Utility` (carga escalada
por la frecuencia turbo: en el i5-12400F, base 2500 MHz corriendo a ~4080 MHz, el
factor es ~1,63) y queda clampeado en 100 con cualquier carga real >= ~61%.
Aca la carga es `% Processor Time` via psutil: la carga real.

Protocolo del panel (reverse-engineered):
    TX  F0 A5 5A 0F                 handshake
    RX  <SN ascii, 26 bytes>
    TX  AA BB <brillo 0..100> CC DD
    TX  <JPEG 320x1480 baseline 4:2:0>    un write por frame

Sensores: psutil (carga CPU, RAM, red) + sensors.ps1 (Gigabyte GSA1 ACPI-WMI para
temp CPU/VRM y VCore; LibreHardwareMonitor para GPU y temps de SSD).
Todas las lecturas de hardware son read-only.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import threading
import time

import psutil
import serial
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'assets')
PORT = 'COM3'
W, H = 320, 1480

WHITE = (255, 255, 255)
BLUE = (134, 182, 239)
GRAY = (137, 135, 129)
BAR_FILL = (57, 135, 229)
BAR_TRACK = (36, 40, 52)

HANDSHAKE = bytes([0xF0, 0xA5, 0x5A, 0x0F])
DASH = '--'


def brightness_cmd(v):
    return bytes([0xAA, 0xBB, max(0, min(100, int(v))), 0xCC, 0xDD])


_fonts = {}
def font(px, bold=False):
    key = (px, bold)
    if key not in _fonts:
        name = 'consolab.ttf' if bold else 'consola.ttf'
        _fonts[key] = ImageFont.truetype(os.path.join(ASSETS, name), px)
    return _fonts[key]


class Sensors:
    """Lee sensors.ps1 en background y mantiene la ultima muestra buena."""

    def __init__(self):
        self.data = {}
        self.last = 0.0
        self.proc = subprocess.Popen(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
             '-File', os.path.join(HERE, 'sensors.ps1')],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                self.data = json.loads(line)
                self.last = time.time()
            except json.JSONDecodeError:
                pass

    @property
    def fresh(self):
        return time.time() - self.last < 8.0

    def get(self, key, default=None):
        return self.data.get(key, default) if self.fresh else default

    def stop(self):
        try:
            self.proc.terminate()
        except Exception:
            pass


class NetRate:
    def __init__(self):
        c = psutil.net_io_counters()
        self.prev = (c.bytes_recv, c.bytes_sent, time.time())

    def sample(self):
        c = psutil.net_io_counters()
        now = time.time()
        dt = max(0.2, now - self.prev[2])
        down = (c.bytes_recv - self.prev[0]) / dt
        up = (c.bytes_sent - self.prev[1]) / dt
        self.prev = (c.bytes_recv, c.bytes_sent, now)
        return down, up


def human_rate(bps):
    if bps >= 1024 * 1024:
        return f"{bps / 1048576:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def num(v, fmt='{:.0f}', suffix=''):
    if v is None:
        return DASH + suffix
    try:
        return fmt.format(v) + suffix
    except (TypeError, ValueError):
        return DASH + suffix


class Renderer:
    def __init__(self):
        self.bg = Image.open(os.path.join(ASSETS, 'back.png')).convert('RGB')

    def bar(self, g, x, y, w, h, pct):
        pct = 0.0 if pct is None else max(0.0, min(100.0, float(pct)))
        g.rounded_rectangle([x, y, x + w, y + h], radius=5, fill=BAR_TRACK)
        fw = int(w * pct / 100.0)
        if fw > 2:
            g.rounded_rectangle([x, y, x + fw, y + h], radius=5, fill=BAR_FILL)

    def frame(self, d):
        im = self.bg.copy()
        g = ImageDraw.Draw(im)

        g.text((18, 20), d['time'], font=font(74, True), fill=WHITE)
        g.text((24, 104), d['date'], font=font(20), fill=GRAY)

        # CPU
        g.text((24, 230), d['cpu_name'], font=font(14), fill=GRAY)
        g.text((20, 248), num(d['cpu_load'], '{:.1f}', '%'), font=font(60), fill=WHITE)
        self.bar(g, 24, 316, 272, 16, d['cpu_load'])
        g.text((22, 382), num(d['cpu_temp'], '{:.0f}', '°'), font=font(28), fill=BLUE)
        g.text((158, 382), num(d['cpu_clock']), font=font(28), fill=BLUE)
        g.text((22, 454), num(d['vcore'], '{:.2f}', 'V'), font=font(28), fill=BLUE)
        g.text((158, 454), num(d['vrm_temp'], '{:.0f}', '°'), font=font(28), fill=BLUE)

        # GPU
        g.text((24, 574), d['gpu_name'], font=font(14), fill=GRAY)
        g.text((20, 592), num(d['gpu_load'], '{:.0f}', '%'), font=font(60), fill=WHITE)
        self.bar(g, 24, 660, 272, 16, d['gpu_load'])
        g.text((22, 726), d['gpu_temps'], font=font(28), fill=BLUE)
        g.text((158, 726), num(d['gpu_clock']), font=font(28), fill=BLUE)
        g.text((22, 798), num(d['gpu_power'], '{:.0f}', 'W'), font=font(28), fill=BLUE)
        g.text((158, 798), num(d['gpu_vram'], '{:.0f}', '%'), font=font(28), fill=BLUE)

        # RAM
        g.text((20, 922), num(d['mem_load'], '{:.1f}', '%'), font=font(60), fill=WHITE)
        self.bar(g, 24, 990, 272, 16, d['mem_load'])
        g.text((22, 1056), num(d['mem_used'], '{:.1f}', 'G'), font=font(28), fill=BLUE)
        g.text((158, 1056), d['mem_speed'], font=font(28), fill=BLUE)

        # SYS
        g.text((22, 1230), d['down'], font=font(26), fill=BLUE)
        g.text((22, 1300), d['up'], font=font(26), fill=BLUE)
        g.text((22, 1370), d['ssd'], font=font(26), fill=BLUE)
        return im


DIAS = ['LUN', 'MAR', 'MIE', 'JUE', 'VIE', 'SAB', 'DOM']
MESES = ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC']


def collect(sens, net, cpu_load):
    t = time.localtime()
    vm = psutil.virtual_memory()
    down, up = net.sample()

    gt, gh = sens.get('gpu_temp'), sens.get('gpu_hotspot')
    if gt is None:
        gpu_temps = DASH + '°'
    elif gh is None:
        gpu_temps = f"{gt:.0f}°"
    else:
        gpu_temps = f"{gt:.0f}/{gh:.0f}°"

    disks = sens.get('disks') or []
    ssd = DASH + '°'
    if disks:
        temps = [x.get('temp') for x in disks if x.get('temp') is not None]
        if temps:
            ssd = '  '.join(f"{v:.0f}°" for v in temps)

    gpu_name = (sens.get('gpu_name') or 'AMD RADEON RX 6800 XT').upper()

    return {
        'time': time.strftime('%H:%M', t),
        'date': f"{DIAS[t.tm_wday]} {t.tm_mday} {MESES[t.tm_mon - 1]}",
        'cpu_name': 'INTEL CORE i5-12400F',
        'cpu_load': cpu_load,
        'cpu_temp': sens.get('cpu_temp'),
        'cpu_clock': sens.get('cpu_clock'),
        'vcore': sens.get('vcore'),
        'vrm_temp': sens.get('vrm_temp'),
        'gpu_name': gpu_name,
        'gpu_load': sens.get('gpu_load'),
        'gpu_temps': gpu_temps,
        'gpu_clock': sens.get('gpu_clock'),
        'gpu_power': sens.get('gpu_power'),
        'gpu_vram': sens.get('gpu_vram'),
        'mem_load': vm.percent,
        'mem_used': vm.used / (1024 ** 3),
        'mem_speed': '6000',
        'down': human_rate(down),
        'up': human_rate(up),
        'ssd': ssd,
    }


ROT = {0: None, 90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180,
       270: Image.Transpose.ROTATE_270}


def to_jpeg(im, rotate=0):
    # el panel esta montado al revez en el gabinete: se manda rotado
    if ROT.get(rotate) is not None:
        im = im.transpose(ROT[rotate])
    b = io.BytesIO()
    im.save(b, format='JPEG', quality=82, subsampling=2)
    return b.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--save', help='render a PNG en vez de mandar al panel')
    ap.add_argument('--fps', type=float, default=1.0)
    ap.add_argument('--brightness', type=int, default=100)
    ap.add_argument('--rotate', type=int, choices=[0, 90, 180, 270], default=180,
                    help='rotacion antes de mandar (el panel esta montado al revez)')
    ap.add_argument('--warmup', type=float, default=25.0,
                    help='segundos max de espera por la primer muestra del sidecar')
    ap.add_argument('--log', help='redirigir stdout/stderr a este archivo (para autostart)')
    a = ap.parse_args()

    if a.log:
        f = open(a.log, 'a', buffering=1, encoding='utf-8')
        sys.stdout = sys.stderr = f
        print(f"\n=== arranque {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)

    r = Renderer()
    sens = Sensors()
    net = NetRate()
    psutil.cpu_percent(interval=None)

    deadline = time.time() + a.warmup
    while not sens.fresh and time.time() < deadline:
        time.sleep(0.5)
    if not sens.fresh:
        print('aviso: sidecar sin datos, los campos de hardware van en "--"', file=sys.stderr)

    if a.save:
        r.frame(collect(sens, net, psutil.cpu_percent(interval=0.5))).save(a.save)
        sens.stop()
        print('guardado', a.save)
        return

    try:
        while True:
            try:
                serve(r, sens, net, a)
                if a.once:
                    break
            except (serial.SerialException, OSError) as e:
                # panel desconectado, puerto tomado por otro proceso, o resume de suspension
                print(f'serial: {e} -- reintento en 5s', flush=True)
                if a.once:
                    raise
                time.sleep(5)
    finally:
        sens.stop()


def serve(r, sens, net, a):
    ser = serial.Serial(PORT, 9600, timeout=1.5, write_timeout=8)
    try:
        ser.write(HANDSHAKE)
        sn = ser.read(26).decode('ascii', 'replace')
        print('panel SN:', sn, flush=True)
        ser.write(brightness_cmd(a.brightness))
        time.sleep(0.1)

        period = 1.0 / max(0.1, a.fps)
        n = 0
        while True:
            t0 = time.time()
            load = psutil.cpu_percent(interval=None)
            jpg = to_jpeg(r.frame(collect(sens, net, load)), a.rotate)
            ser.write(jpg)
            ser.flush()
            n += 1
            if n % 300 == 1:
                print(f'frame {n} {len(jpg)}B cpu={load:.1f}% '
                      f'sidecar={"ok" if sens.fresh else "STALE"}', flush=True)
            if a.once:
                break
            time.sleep(max(0.05, period - (time.time() - t0)))
    finally:
        ser.close()


if __name__ == '__main__':
    main()
