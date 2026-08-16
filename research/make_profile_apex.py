"""Genera vmaxpanel/profiles/apex.json: el perfil "gamer" con todo.

Se genera con un script y no a mano porque son ~120 widgets y la mitad son
repeticiones por instancia (6 nucleos, 4 volumenes, 3 SSD). Editarlos a mano
garantiza inconsistencias de 1 px; generarlos garantiza la grilla.

    python research/make_profile_apex.py

DIRECCION DE DISENO
-------------------
El panel es un instrumento, no una tarjeta de dashboard. Tres decisiones que
salen de medir, no de gusto:

1. **Riel derecho.** Bahnschrift (DIN 1451, la de las senales alemanas) NO tiene
   digitos tabulares: el "1" mide 84 px y el resto 128-136, asi que un
   porcentaje en vivo saltaria al pasar de 11% a 47%. Alineado a la derecha el
   borde queda fijo y el numero crece hacia la izquierda, que es como lo hace un
   instrumento de verdad. Todos los valores grandes aterrizan en x=296.

2. **El color es estado, no decoracion.** En este motor solo los widgets `text`
   tienen reglas de color, asi que la geometria (barras, trazas, hairlines) es
   monocroma y son los NUMEROS los que se ponen ambar y rojo. Un panel que se
   mira de reojo: si no hay nada naranja, no hay nada que mirar.

3. **Trazas, no solo instantaneas.** El widget `graph` guarda 320 muestras y el
   perfil anterior no usaba ninguna. Ver el pico que acaba de pasar es mas util
   que el numero de este segundo, y es lo que hace que el panel se lea como un
   registrador de tira continua en vez de cinco tarjetas apiladas.

La firma es el **canal izquierdo**: una hairline vertical de 1480 px con marcas
cada 40, como el borde del papel de un registrador. Es lo que hace que las siete
secciones se lean como UN instrumento y no como una pila de bloques.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vmaxpanel.layout import loader, schema                          # noqa: E402

DESTINO = Path(__file__).resolve().parent.parent / "vmaxpanel/profiles/apex.json"

# --- paleta ---
#
# Naranja #FF4D00 pedido por el usuario. Cambiar el acento obligo a rearmar la
# escala de alarma: la regla del perfil es "el color es estado", y con el acento
# ya en naranja un ambar de atencion queda indistinguible del acento. La escalada
# pasa a leerse como metal calentandose -- blanco calido en calma, naranja cuando
# hay que mirar, ambar brillante cuando urge -- que ademas es la unica direccion
# donde los tres pasos se distinguen entre si sin salirse de la familia.
#
# Los grises tambien son calidos: un naranja sobre grises azulados se ve pegado
# encima en vez de formar parte de la misma imagen.
NARANJA = "#FF4D00"     # el acento: trazas, barras, hairline de seccion
BASE = "#0A0705"        # fondo, casi negro con tinte calido
BANDA = "#150F0B"       # bandas de seccion
LINEA = "#2A1D16"       # hairlines
LABEL = "#9A8578"       # rotulos, gris calido
VALOR = "#F6EEE8"       # numeros en calma, blanco calido
TENUE = "#7A6558"       # datos de contexto
PISTA = "#33190C"       # track de las barras: el acento apagado
AMBAR = "#FFC400"       # alarma: mas caliente que el acento, no otro color
ROJO = AMBAR            # se conserva el nombre para no tocar los umbrales

# --- grilla ---
#
# El canal izquierdo con marcas cada 40 px -- la "firma" de la primera version --
# se fue. Pretendia ser el borde del papel de un registrador de tira, pero esas
# marcas NO CODIFICAN NADA: no senalan valores ni tiempos, eran decoracion, y el
# usuario razonablemente pregunto que eran. Duplicarlas del otro lado para hacerlo
# simetrico habria sido duplicar algo que no significa nada. Queda un marco de
# 1 px a cada lado, que si es simetrico y no pretende decir nada.
ANCHO, ALTO = 320, 1480
MARCO = 6               # las dos hairlines de borde, simetricas
IZQ = 20                # donde arranca el contenido
RIEL = 300              # donde aterrizan los valores alineados a la derecha
CENTRO = ANCHO // 2     # para lo que va centrado en el panel, como el reloj
ANCHO_UTIL = RIEL - IZQ

FUENTES = {
    # DIN para los numeros grandes: instrumentacion, no dashboard.
    "hero": {"family": "Bahnschrift", "size": 66},
    "big": {"family": "Bahnschrift", "size": 46},
    "mid": {"family": "Bahnschrift", "size": 30},
    # Condensada tabular para los datos densos: entra mas y las columnas no
    # bailan al cambiar de digito.
    "dato": {"family": "Franklin Gothic Medium Cond", "size": 21},
    "dato-s": {"family": "Franklin Gothic Medium Cond", "size": 17},
    # Rotulos: la misma familia en Demi, para que sean del mismo mundo.
    "rotulo": {"family": "Franklin Gothic Demi Cond", "size": 16},
    "eyebrow": {"family": "Franklin Gothic Demi Cond", "size": 22},
    "micro": {"family": "Franklin Gothic Medium Cond", "size": 14},
}

widgets = []


def w(**kw):
    widgets.append(kw)


def rect(id, x, y, ancho, alto, fill=LINEA, **kw):
    w(id=id, type="rect", x=x, y=y, w=ancho, h=alto, fill=fill, **kw)


def rotulo(id, texto, x, y, font="rotulo", color=LABEL, align="left"):
    w(id=id, type="label", text=texto, x=x, y=y, font=font, color=color,
      align=align)


def valor(id, metric, x, y, fmt, font="dato", color=VALOR, align="right",
          rules=None, humanize=None):
    d = dict(id=id, type="text", metric=metric, x=x, y=y, font=font,
             color=color, format=fmt, align=align)
    if rules:
        d["rules"] = rules
    if humanize:
        d["humanize"] = humanize
    w(**d)


def barra(id, metric, x, y, ancho, alto, fill=NARANJA, **kw):
    w(id=id, type="bar", metric=metric, x=x, y=y, w=ancho, h=alto,
      radius=0, fill=fill, track=PISTA, **kw)


def traza(id, metric, x, y, ancho, alto, color=NARANJA, samples=240,
          track=None, **kw):
    # `track` explicito: sin el, GraphWidget cae a su default "#242834", un gris
    # AZULADO. Con el resto de la paleta en calido, los cuatro fondos de traza
    # quedaban frios y se veian pegados de otra imagen.
    w(id=id, type="graph", metric=metric, x=x, y=y, w=ancho, h=alto,
      color=color, samples=samples, track=track or PISTA, **kw)


def fans_que_giran(limite=4):
    """Los conectores de ventilador con una lectura real, leyendo la maquina.

    No se adivina: se levanta el registry una vez y se mira que fan devuelve algo
    distinto de cero. Un conector libre reporta 0, y cuatro columnas con tres
    ceros no informan nada -- dicen que sobran conectores, que no es un dato de
    monitoreo.

    Si no hay sidecar (otra maquina, sin los DLL, sin permisos) se cae al fan 1,
    que es CPU_FAN en las placas Gigabyte: es la suposicion menos mala y el
    perfil sigue validando.
    """
    try:
        from vmaxpanel.providers.setup import build_registry
        reg, cliente = build_registry(warmup=30.0)
        try:
            muestra = reg.read()
        finally:
            reg.close()
            if cliente is not None:
                cliente.close()
    except Exception as e:
        print(f"  aviso: no se pudieron leer los ventiladores ({e}); se asume el 1")
        return [1]
    activos = [n for n in range(1, limite + 1)
               if isinstance(muestra.get(f"fan.{n}.rpm"), (int, float))
               and muestra[f"fan.{n}.rpm"] > 0]
    print(f"  ventiladores con lectura: {activos or 'ninguno'}")
    return activos


FANS_ACTIVOS = fans_que_giran()

# Umbrales: el mismo criterio en todo el panel, asi que se declaran una vez.
CALIENTE = [{"when": "> 80", "color": NARANJA}, {"when": "> 90", "color": AMBAR}]
CARGADO = [{"when": "> 85", "color": NARANJA}, {"when": "> 95", "color": AMBAR}]
LLENO = [{"when": "> 85", "color": NARANJA}, {"when": "> 95", "color": AMBAR}]


# Cursor de altura. Las secciones se apilan y cada una declara cuanto contenido
# tiene, en vez de llevar una y fija: con coordenadas a mano la primera vez que
# se acorta una seccion queda un hueco muerto y la de abajo no se entera.
CURSOR = [140]          # debajo del reloj y su fila de leyendas
SEPARACION = 14         # 12 dejaba las secciones respirando apenas


def seccion(nombre, texto, alto_contenido, sufijo=None, sufijo_metric=None,
            fmt_sufijo="{}"):
    """Banda + eyebrow + hairline. Devuelve la y del primer contenido."""
    y = CURSOR[0]
    alto = alto_contenido + 34
    rect(f"{nombre}-banda", MARCO + 2, y, ANCHO - 2 * (MARCO + 2), alto,
         fill=BANDA)
    rect(f"{nombre}-linea", MARCO + 2, y, ANCHO - 2 * (MARCO + 2), 1, fill=NARANJA)
    rotulo(f"{nombre}-eyebrow", texto, IZQ, y + 8, font="eyebrow", color=VALOR)
    if sufijo_metric:
        valor(f"{nombre}-sufijo", sufijo_metric, RIEL, y + 12, fmt_sufijo,
              font="micro", color=TENUE)
    elif sufijo:
        rotulo(f"{nombre}-sufijo", sufijo, RIEL, y + 12, font="micro",
               color=TENUE, align="right")
    CURSOR[0] = y + alto + SEPARACION
    return y + 34


def fila_datos(prefijo, y, columnas, alto_fila=46):
    """Una fila de pares rotulo/valor, cada par CENTRADO en su columna.

    Centrado y no alineado a la izquierda: con columnas de 90 px y valores de
    40, alinear a la izquierda deja todo el peso contra el borde izquierdo de
    cada celda y un hueco a la derecha, asi que la fila se lee corrida en vez de
    compuesta. Centrado, la columna existe visualmente aunque no se dibuje
    ninguna linea.
    """
    n = len(columnas)
    paso = ANCHO_UTIL // n
    for i, col in enumerate(columnas):
        centro = IZQ + i * paso + paso // 2
        if col.get("rotulo_metric"):
            # El rotulo tambien sale de una metrica: la columna se nombra sola
            # con lo que hay en ESTA maquina. Es lo que necesitan las temperaturas
            # de SSD, donde un "SSD 1" escrito a mano nombra la posicion en la
            # enumeracion de LibreHardwareMonitor y no la unidad.
            valor(f"{prefijo}-r{i}", col["rotulo_metric"], centro, y, "{}",
                  font="micro", color=LABEL, align="center")
        else:
            rotulo(f"{prefijo}-r{i}", col["rotulo"], centro, y, font="micro",
                   color=LABEL, align="center")
        if col.get("metric"):
            valor(f"{prefijo}-v{i}", col["metric"], centro, y + 16, col["fmt"],
                  font=col.get("font", "dato"), align="center",
                  rules=col.get("rules"), humanize=col.get("humanize"))
        else:
            rotulo(f"{prefijo}-v{i}", col["texto"], centro, y + 16,
                   font=col.get("font", "dato"), color=VALOR, align="center")
    return y + alto_fila


# ==========================================================================
# El canal izquierdo: la firma. Hairline continua de 1480 px con marcas cada
# 40, como el borde del papel de un registrador de tira. Es lo que une las
# siete secciones en un solo instrumento.
# ==========================================================================
rect("marco-izq", MARCO, 0, 1, ALTO, fill=LINEA)
rect("marco-der", ANCHO - 1 - MARCO, 0, 1, ALTO, fill=LINEA)

# ==========================================================================
# HEADER
# ==========================================================================
# El reloj manda y las dos leyendas van debajo, centradas en su mitad. La version
# anterior las apilaba contra el margen izquierdo con el contador de procesos al
# lado, a media altura: esa columna suelta no se alineaba con nada y se leia como
# un pegote debajo del reloj. El contador de procesos se fue a SISTEMA, que es
# donde vive el resto de los datos del equipo.
# El reloj CENTRADO en el panel y no contra el riel derecho. El riel existe para
# que los valores en vivo no bailen al cambiar de digito (Bahnschrift no es
# tabular), pero la hora es lo primero que se mira y no compite con nada a su
# lado: ahi manda la simetria, no la grilla de datos.
valor("hora", "clock.time_hms", CENTRO, 18, "{}", font="hero", color=VALOR,
      align="center")
fila_datos("head", 84, [
    {"rotulo": "FECHA", "metric": "clock.date", "fmt": "{}", "font": "dato-s"},
    {"rotulo": "ENCENDIDA", "metric": "sys.uptime", "fmt": "{}",
     "font": "dato-s", "humanize": "duration"},
])

# ==========================================================================
# CPU
# ==========================================================================
# Las alturas salen de lo que dibuja cada fila -- 46 px una fila de datos, 18 un
# nucleo -- y no de estimarlas a ojo: al subir el interlineado, las alturas viejas
# dejaron la ultima fila cortada por la banda de la seccion siguiente.
y = seccion("cpu", "CPU", 356, sufijo_metric="cpu.name_short")
# El numero se lleva su propia fila y la traza todo el ancho. Superpuestos, las
# lineas de la traza cruzaban los digitos y ninguno de los dos se leia: la traza
# es lo distintivo de este perfil, asi que gana el ancho completo.
valor("cpu-load", "cpu.load", RIEL, y - 6, "{:.0f}%", font="hero", color=VALOR,
      rules=CARGADO)
barra("cpu-bar", "cpu.load", IZQ, y + 62, ANCHO_UTIL, 9)
traza("cpu-traza", "cpu.load", IZQ, y + 78, ANCHO_UTIL, 46)
y2 = fila_datos("cpu-f1", y + 134, [
    {"rotulo": "TEMP", "metric": "cpu.temp", "fmt": "{:.0f}°", "rules": CALIENTE},
    {"rotulo": "CLOCK", "metric": "cpu.clock", "fmt": "{:.0f}"},
    {"rotulo": "POWER", "metric": "cpu.power", "fmt": "{:.0f}W"},
])
y2 = fila_datos("cpu-f2", y2, [
    {"rotulo": "VCORE", "metric": "cpu.vcore", "fmt": "{:.2f}V"},
    {"rotulo": "VRM", "metric": "cpu.vrm_temp", "fmt": "{:.0f}°",
     "rules": CALIENTE},
    {"rotulo": "FAN", "metric": "cpu.fan", "fmt": "{:.0f}"},
])
# La escalera por nucleo: 6 filas de barra + temperatura. Es el dato que ningun
# panel del vendor podia mostrar y el que dice de verdad como esta el CPU.
rotulo("core-r", "CARGA Y TEMPERATURA POR NÚCLEO", IZQ, y2 - 4, font="micro")
yc = y2 + 16
for n in range(1, 7):
    # 18 px por fila en vez de 15: seis filas apretadas a 15 con texto de 17
    # dejaban los numeros tocandose entre renglones.
    fy = yc + (n - 1) * 18
    rotulo(f"core-{n}-n", str(n), IZQ, fy, font="micro", color=TENUE)
    barra(f"core-{n}-bar", f"core.{n}.load", IZQ + 16, fy + 4, 194, 7)
    valor(f"core-{n}-t", f"core.{n}.temp", RIEL, fy - 1, "{:.0f}°",
          font="dato-s", rules=CALIENTE)
# La frecuencia por nucleo estuvo y se fue: seis numeros de cuatro digitos en una
# fila es ruido, y el clock que importa ya esta arriba en CLOCK. La escalera de
# carga + temperatura es lo que dice algo de un nucleo; su frecuencia individual
# solo importa si estas depurando el boost, y para eso no se mira un panel.

# ==========================================================================
# GPU
# ==========================================================================
y = seccion("gpu", "GPU", 228, sufijo_metric="gpu.name")
valor("gpu-load", "gpu.load", RIEL, y - 6, "{:.0f}%", font="hero", color=VALOR,
      rules=CARGADO)
barra("gpu-bar", "gpu.load", IZQ, y + 62, ANCHO_UTIL, 9)
traza("gpu-traza", "gpu.load", IZQ, y + 78, ANCHO_UTIL, 46)
y2 = fila_datos("gpu-f1", y + 134, [
    {"rotulo": "TEMP", "metric": "gpu.temp", "fmt": "{:.0f}°", "rules": CALIENTE},
    {"rotulo": "HOTSPOT", "metric": "gpu.hotspot", "fmt": "{:.0f}°",
     "rules": [{"when": "> 95", "color": NARANJA}, {"when": "> 105", "color": AMBAR}]},
    {"rotulo": "CLOCK", "metric": "gpu.clock", "fmt": "{:.0f}"},
])
fila_datos("gpu-f2", y2, [
    {"rotulo": "POWER", "metric": "gpu.power", "fmt": "{:.0f}W"},
    {"rotulo": "VRAM", "metric": "gpu.vram", "fmt": "{:.0f}%", "rules": LLENO},
    {"rotulo": "FAN", "metric": "gpu.fan", "fmt": "{:.0f}"},
])

# ==========================================================================
# MEMORIA
# ==========================================================================
y = seccion("ram", "MEMORIA", 94, sufijo_metric="mem.speed",
            fmt_sufijo="{:.0f} MT/s")
barra("ram-bar", "mem.load", IZQ, y + 40, ANCHO_UTIL, 9)
valor("ram-load", "mem.load", RIEL, y - 8, "{:.0f}%", font="big", color=VALOR,
      rules=LLENO)
fila_datos("ram-f1", y + 56, [
    {"rotulo": "EN USO", "metric": "mem.used", "fmt": "{:.1f} GiB"},
    {"rotulo": "TOTAL", "metric": "mem.total", "fmt": "{:.0f} GiB"},
])

# ==========================================================================
# ALMACENAMIENTO: una fila por volumen, con su nombre real
# ==========================================================================
# G: es Google Drive: un disco virtual, no hardware de la maquina, y su ocupacion
# no dice nada sobre el equipo. Solo los discos fisicos.
VOLS = [("C", "SISTEMA"), ("D", "JUEGOS"), ("E", "DATOS")]
y = seccion("disk", "ALMACENAMIENTO", 160)
for i, (letra, nombre) in enumerate(VOLS):
    fy = y + i * 38
    rotulo(f"vol-{letra}-l", f"{letra}:", IZQ, fy, font="rotulo", color=VALOR)
    rotulo(f"vol-{letra}-n", nombre, IZQ + 28, fy + 2, font="micro")
    # "usado / total" y no "libres": un numero solo no dice si 270 GiB es medio
    # disco o el 5%. Son dos widgets porque cada uno muestra UNA metrica, y el
    # slash va DENTRO del formato del total -- no como un widget aparte con x
    # fijo, que fue el primer intento y se solapaba con los dos numeros.
    # Anclados los dos a la derecha, el par se lee como una sola cosa.
    valor(f"vol-{letra}-usado", f"vol.{letra}.used", RIEL - 76, fy - 2,
          "{:.0f}", font="dato-s")
    valor(f"vol-{letra}-total", f"vol.{letra}.total", RIEL, fy - 2,
          "/ {:.0f} GiB", font="dato-s", color=TENUE)
    barra(f"vol-{letra}-bar", f"vol.{letra}.load", IZQ, fy + 21, ANCHO_UTIL, 6)
# Las temperaturas, centradas en tres columnas. Sueltas y alineadas a la
# izquierda cada 70 px no se correspondian con nada de arriba y se leian como
# tres numeros tirados.
TEMP_SSD = [{"when": "> 60", "color": NARANJA}, {"when": "> 70", "color": AMBAR}]
fila_datos("ssd", y + len(VOLS) * 38 + 8, [
    {"rotulo_metric": f"disk.name.{n}", "metric": f"disk.temp.{n}",
     "fmt": "{:.0f}°", "font": "dato-s", "rules": TEMP_SSD} for n in range(3)])

# ==========================================================================
# RED: dos trazas, porque lo que importa es el pico, no el instante
# ==========================================================================
y = seccion("net", "RED", 124, sufijo="ETHERNET")
# max explicito: net.down/up declaran max=None porque una tasa no tiene techo
# natural, y sin techo el graph no puede escalar y no dibuja NADA. El techo de la
# traza es una decision de lectura, no del sensor: 10 MB/s de bajada y 2 de
# subida es el rango donde vive el trafico de esta maquina.
TECHO_DOWN, TECHO_UP = 10_000_000, 2_000_000
# Las trazas miden 26 px y no 36, y arrancan 8 px mas abajo: un pico llegaba al
# tope del grafico y tocaba los descendentes del "1.2 MB/s" de arriba. Un grafico
# que se mete en el numero que explica es peor que un grafico mas chico.
rotulo("net-d-r", "BAJADA", IZQ, y, font="micro")
traza("net-d-traza", "net.ethernet.down", IZQ, y + 26, ANCHO_UTIL, 26,
      samples=180, max=TECHO_DOWN)
valor("net-d", "net.ethernet.down", RIEL, y - 6, "{}", font="mid", color=NARANJA,
      humanize="rate")
rotulo("net-u-r", "SUBIDA", IZQ, y + 62, font="micro")
traza("net-u-traza", "net.ethernet.up", IZQ, y + 88, ANCHO_UTIL, 26,
      samples=180, max=TECHO_UP)
valor("net-u", "net.ethernet.up", RIEL, y + 56, "{}", font="mid", color=NARANJA,
      humanize="rate")

# ==========================================================================
# PLACA
# ==========================================================================
y = seccion("mb", "SISTEMA", 90, sufijo="B760M D3HP")
# Los ventiladores VACIOS no entran. Cuatro columnas con tres ceros no informan
# nada: dicen que hay conectores libres, que no es un dato de monitoreo. Cuales
# giran no se adivina, se lee de la maquina al generar (ver FANS_ACTIVOS).
columnas = [
    {"rotulo": "PLACA", "metric": "mb.temp.0", "fmt": "{:.0f}°",
     "rules": CALIENTE},
    {"rotulo": "VRM", "metric": "mb.temp.1", "fmt": "{:.0f}°", "rules": CALIENTE},
]
columnas += [{"rotulo": f"FAN {n}", "metric": f"fan.{n}.rpm", "fmt": "{:.0f}"}
             for n in FANS_ACTIVOS]
columnas.append({"rotulo": "PROCESOS", "metric": "sys.procs", "fmt": "{:.0f}"})
fila_datos("sys-f1", y, columnas[:3])
if len(columnas) > 3:
    fila_datos("sys-f2", y + 48, columnas[3:])
# Cierre: la misma hairline cyan del tope de cada seccion, para cerrar la pila.
rect("cierre", MARCO + 2, min(ALTO - 8, CURSOR[0] - SEPARACION),
     ANCHO - 2 * (MARCO + 2), 1, fill=LINEA)

PERFIL = {
    "version": 1,
    "name": "Apex",
    "designed_for": {"width": ANCHO, "height": ALTO},
    "panel": {"rotate": 180, "brightness": 100, "fps": 30, "jpeg_quality": 88},
    "fonts": FUENTES,
    # Fondo animado: una BANDA que barre, no un degradado que se desliza.
    #
    # Dos intentos fallaron y el segundo se midio: a 6 y despues a 14 px/s con un
    # gradiente suave, el fondo SI animaba -- los pixeles cambiaban -- pero el
    # delta era de 1 nivel de color a los 0,5 s y el ciclo completo tardaba 211
    # segundos. Invisible. El problema no era solo la velocidad: un degradado
    # suave desplazandose cambia cada pixel en 1-2 niveles, asi que nunca se ve
    # "pasar" nada.
    #
    # Lo que se ve moverse es un BORDE, no un degradado. Y van DOS bandas, no
    # una: con una sola, a 150 px/s sobre una tira de 2960, el panel quedaba
    # oscuro 13 de cada 20 segundos y mirar en el momento equivocado era volver a
    # no ver nada. Con dos siempre hay una cruzando.
    #
    # Para bajarle el volumen: `speed` mas chico y el pico (#6B2408) mas cerca
    # del fondo. Los dos se editan en la pestana Fondo del editor, sin regenerar.
    "background": {"type": "procedural", "name": "scroll", "speed": 150,
                   "angle": 90,
                   "stops": [{"at": 0.0, "color": BASE},
                             {"at": 0.16, "color": BASE},
                             {"at": 0.20, "color": "#2A1206"},
                             {"at": 0.24, "color": "#6B2408"},
                             {"at": 0.28, "color": "#2A1206"},
                             {"at": 0.32, "color": BASE},
                             {"at": 0.66, "color": BASE},
                             {"at": 0.70, "color": "#2A1206"},
                             {"at": 0.74, "color": "#6B2408"},
                             {"at": 0.78, "color": "#2A1206"},
                             {"at": 0.82, "color": BASE},
                             {"at": 1.0, "color": BASE}]},
    "widgets": widgets,
}


def main():
    errores = schema.validate(PERFIL)
    if errores:
        print(f"NO VALIDA ({len(errores)} errores):")
        for e in errores[:12]:
            print("  -", e)
        raise SystemExit(1)
    loader.save_raw(PERFIL, DESTINO)
    print(f"guardado {DESTINO}")
    print(f"  {len(widgets)} widgets, {len(FUENTES)} alias de fuente")
    metricas = sorted({x['metric'] for x in widgets if x.get('metric')})
    print(f"  {len(metricas)} metricas distintas")
    print(f"  el contenido termina en y={CURSOR[0] - SEPARACION} de {ALTO}")
    tipos = {}
    for x in widgets:
        tipos[x["type"]] = tipos.get(x["type"], 0) + 1
    print("  por tipo:", tipos)


if __name__ == "__main__":
    main()
