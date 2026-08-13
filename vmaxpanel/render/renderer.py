"""Compone el frame del panel: fondo + widgets.

Un solo renderer para el servicio y para el editor. Si hubiera dos
implementaciones divergirian y el preview del editor terminaria mintiendo.

La escala es uniforme (min de los dos ejes) y se aplica tambien al tamano de
fuente: escalar los ejes por separado deformaria el texto. Cuando la relacion
de aspecto del panel real difiere de `designed_for`, el contenido escalado
(los widgets) queda mas chico que el lienzo destino en un eje; ese sobrante
se reparte mitad y mitad para centrar en vez de amontonar todo contra una
esquina -- asi lo dice el doc de diseno y asi lo prueba
test_scale_uses_the_smaller_axis_and_centers, aunque el codigo original de
esta tarea nunca calculaba el offset.
"""
import io
import math
from collections import defaultdict, deque
from pathlib import Path

from PIL import Image

from ..layout.model import Size
from . import widgets as W
from .background import BackgroundSource
from .fonts import FontResolver

DEFAULT_ASSETS = Path(__file__).resolve().parent.parent / "assets"

ROTATIONS = {
    0: None,
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}


def _finite_number(v):
    """Numero utilizable para el historial. Mismo criterio que
    widgets._num(): rechaza bool (isinstance(True, int) es True) y NaN/Inf.
    Un sensor fallado que empuja nan no puede quedar en el ring buffer como
    si fuera un dato real -- un widget de graph futuro que promediara la
    serie en vez de puntearla, como hace hoy widgets._draw_graph, terminaria
    contaminado por un solo nan.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return math.isfinite(v)


def _aviso_fuente(font, resolver) -> str:
    """El aviso de una familia ausente, diciendo con QUE se dibujo si hubo reemplazo.

    "falta X" obliga al usuario a adivinar que esta mirando; "falta X, se uso Y" le
    dice exactamente lo que ve. Y si la cadena del perfil tampoco alcanzo, el aviso lo
    dice, porque entonces lo que se ve es la fuente por defecto de PIL y no se parece
    a nada de lo pedido.
    """
    usada = resolver.substitutions().get(font.family)
    if usada:
        return f"font not found: {font.family} (used {usada})"
    if font.fallbacks:
        return (f"font not found: {font.family}, nor "
                f"{', '.join(font.fallbacks)}")
    return f"font not found: {font.family}"


class History:
    """Ventana deslizante por metrica, para los widgets de tipo graph."""

    def __init__(self, maxlen: int = 320):
        self.maxlen = maxlen
        self._d = defaultdict(lambda: deque(maxlen=maxlen))

    def push(self, sample: dict):
        for mid, v in sample.items():
            if _finite_number(v):
                self._d[mid].append(v)

    def series(self) -> dict:
        return {k: list(v) for k, v in self._d.items()}


class Renderer:
    """El unico renderer del proyecto: lo usa el servicio y, en fase 3, el
    editor para su preview en vivo. Ambos tienen que ver exactamente lo
    mismo, asi que esta clase no puede tener una segunda implementacion en
    otro lado.

    `set_panel_size(panel_size)` cambia solo el tamano real del panel sin
    tocar el layout activo (recalcula todo delegando en set_layout()); no
    aparece en la lista de interfaces del brief de esta tarea, pero es API
    publica igual -- pensada para un editor que deja el layout fijo y
    prueba distintos tamanos de panel.
    """

    def __init__(self, layout, panel_size: Size | None = None, assets_dir=DEFAULT_ASSETS):
        self.assets_dir = Path(assets_dir)
        self._fonts = FontResolver()
        self._panel_size = panel_size
        self.set_layout(layout)

    def set_layout(self, layout) -> None:
        """Reemplaza el layout activo. Total a proposito: recalcula escala,
        offset de centrado y reconstruye el fondo cacheado, para que no
        quede ningun estado del layout anterior mezclado con el nuevo (el
        propio BackgroundSource no se entera de cambios solo -- el
        comentario en background.py dice explicitamente que el dueno es
        quien tiene que descartarlo y crear uno nuevo).
        """
        self.layout = layout
        d = layout.designed_for
        target = self._panel_size or d
        self.scale = min(target.width / d.width, target.height / d.height)

        # self.size es el tamano real del lienzo que el panel espera (nunca
        # depende de redondeos de escala): si target ya viene de un entero,
        # size tiene que coincidir con ese entero exacto, no con
        # round(d.width * scale) que podria quedar 1px corto o largo por
        # arrastre de punto flotante (scale = min(...) no siempre reproduce
        # target/d de forma exacta al multiplicar de vuelta).
        self.size = Size(int(target.width), int(target.height))

        # Contenido escalado (los widgets) dentro de ese lienzo. round(), no
        # int(): la misma razon que _fit() en background.py usa round() para
        # cover -- un scale que no cae exacto puede dar 199.9999999999997 y
        # truncar deja un borde de 1px sin cubrir. Se clampea al tamano del
        # lienzo por si el redondeo empuja 1px de mas.
        cw = min(self.size.width, max(1, round(d.width * self.scale)))
        ch = min(self.size.height, max(1, round(d.height * self.scale)))
        self._content_size = (cw, ch)
        self._offset = ((self.size.width - cw) // 2, (self.size.height - ch) // 2)
        # Atajo real: cuando el contenido llena el lienzo entero (el caso
        # comun -- panel_size None o con la misma relacion de aspecto que
        # designed_for) no hace falta la capa RGBA intermedia para centrar.
        self._exact_fit = self._content_size == (self.size.width, self.size.height)

        # El fondo anterior se cierra ANTES de crear el nuevo: un fondo de video
        # tiene un ffmpeg atras, y set_layout() es el camino de la recarga en
        # caliente, o sea que sin esto cada edicion del perfil sumaria un proceso
        # decodificando para nadie.
        self._cerrar_fondo()
        self._bg = BackgroundSource(layout.background, self.size, self.assets_dir)
        # Fuerza el build del fondo ahora, no en el primer frame(): _build()
        # es quien agrega los warnings de fondo degradado (asset faltante,
        # tipo de fase 2, etc), y BackgroundSource los cachea para siempre
        # despues del primer frame() (ver su docstring). Sin esto,
        # warnings() llamado antes de cualquier frame() veria una lista de
        # fondo vacia aunque el fondo SI tenga un problema real. El costo es
        # un build + una copia de mas por cambio de layout, no por cuadro.
        self._bg.frame()

        # Precalienta el resolver con las fuentes que este layout va a usar,
        # en la escala que este renderer tiene fija. warnings() ya no
        # depende de este precalentamiento (ver mas abajo por que), asi que
        # esto es puramente una optimizacion: evita pagar la carga de la
        # fuente -- abrir el archivo, parsear el TTF -- dentro del primer
        # frame() en vez de aca, donde a 1-10 fps no se nota tanto como
        # adentro del loop de render.
        for font in layout.fonts.values():
            self._fonts.resolve(font, self.scale)

    def _cerrar_fondo(self) -> None:
        """Cierra el fondo activo si hay uno.

        getattr y no self._bg directo: __init__ llama a set_layout(), asi que la
        primera vuelta pasa por aca antes de que el atributo exista. Y se traga
        las excepciones porque esto corre en el camino de la recarga en caliente:
        un fondo que no se deja cerrar no puede impedir que entre el layout
        nuevo.
        """
        bg = getattr(self, "_bg", None)
        if bg is None:
            return
        try:
            bg.close()
        except Exception:
            pass

    def close(self) -> None:
        """Suelta los recursos del fondo. El renderer no dibuja mas, pero
        warnings() sigue contestando: es lo que la bandeja pinta al abrir el menu,
        desde OTRO hilo que el que baja el motor. "El motor se cerro justo cuando
        abriste el menu" no puede ser una excepcion, y los avisos del fondo que se
        cerro son justo el motivo por el que quedo asi.
        """
        bg = getattr(self, "_bg", None)
        if bg is not None:
            self._avisos_fondo = list(bg.warnings)
        self._cerrar_fondo()
        self._bg = None

    def set_panel_size(self, panel_size: Size | None) -> None:
        self._panel_size = panel_size
        self.set_layout(self.layout)

    def warnings(self) -> list[str]:
        """Fondo degradado, fuentes ausentes y directorios de fuentes
        ilegibles. Los dos primeros ya estaban en el brief; el tercero
        (`unreadable_dirs()`) lo expone FontResolver desde la tarea 7 pero
        el brief nunca lo miraba -- es la misma clase de degradacion
        silenciosa que missing() reporta (una familia no aparece porque su
        carpeta no se pudo leer, no porque no exista) y por eso se agrega
        aca tambien.

        Las fuentes ausentes se recalculan DERIVANDOLAS de
        `self.layout.fonts` en cada llamada, en vez de leer un
        `FontResolver.missing()` acumulado. missing() solo anota lo que un
        resolve() anterior efectivamente vio faltar -- y `resolve()`
        devuelve directo desde la cache en un hit, sin volver a pasar por
        ahi -- asi que un FontResolver de larga vida (el de este Renderer,
        reusado a traves de sucesivos set_layout()) puede tener una familia
        cacheada de una vuelta anterior y quedarse mudo sobre ella la
        proxima vez, aunque siga faltando y el layout activo siga
        nombrandola. is_available() no tiene ese problema: es una consulta
        pura contra el indice de fuentes, no un historial de llamadas a
        resolve(), asi que da la misma respuesta la primera vez que se
        pregunta o la enesima. Con esto no hace falta ningun estado propio
        de "que layout pidio que" ni resetearlo entre llamadas a
        set_layout() -- ese estado fue, en dos vueltas de revision
        distintas, la fuente de un warning que sobrevivia de mas y de uno
        que desaparecia de menos.
        """
        # Deduplicado por casing: dos alias que piden la misma familia escrita
        # distinto ("Arial" y "ARIAL") daban dos lineas identicas para el
        # usuario. Se conserva la primera forma vista, que es la que el layout
        # escribio, para que el aviso coincida con lo que el usuario tipeo.
        missing = {}
        for f in self.layout.fonts.values():
            if not self._fonts.is_available(f.family):
                missing.setdefault(f.family.lower(), f)
        avisos_fondo = (list(self._bg.warnings) if self._bg is not None
                        else list(getattr(self, "_avisos_fondo", [])))
        return (avisos_fondo
                + [_aviso_fuente(f, self._fonts)
                   for f in sorted(missing.values(), key=lambda x: x.family.lower())]
                + [f"directorio de fuentes ilegible: {d}"
                   for d in sorted(self._fonts.unreadable_dirs())])

    def frame(self, sample: dict, history: dict | None = None) -> Image.Image:
        img = self._bg.frame()
        ctx = W.DrawCtx(fonts=self._fonts, layout=self.layout, scale=self.scale,
                        assets_dir=self.assets_dir, history=history or {})

        if self._exact_fit:
            # Caso comun: el contenido ya llena el lienzo, se dibuja directo
            # sobre la copia del fondo y no hay que pagar una capa RGBA
            # extra por frame. A 1 fps no se nota; a los ~10 fps que fase 2
            # quiere para el mismo Renderer, evitar una asignacion e
            # composicion de mas por cuadro cuando no hace falta si importa.
            target = img
        else:
            target = Image.new("RGBA", self._content_size, (0, 0, 0, 0))

        for w in self.layout.widgets:
            metric = getattr(w, "metric", None)
            value = sample.get(metric) if metric else None
            W.draw(target, w, value, ctx)

        if not self._exact_fit:
            img.paste(target, self._offset, target)
        return img


def to_jpeg(img: Image.Image, rotate: int = 0, quality: int = 82) -> bytes:
    """JPEG baseline 4:2:0 crudo: es exactamente lo que el panel espera --
    arranca en FFD8FF y termina en FFD9, sin ningun contenedor alrededor.

    El panel de esta maquina esta montado al revez, de ahi el rotate=180 del
    perfil. En otro gabinete puede ser 0: la rotacion es un parametro, nunca
    una asuncion de este modulo.

    subsampling=2 es 4:2:0 en la convencion de Pillow (0=4:4:4, 1=4:2:2,
    2=4:2:0). progressive=False se pasa explicito -- es el default de
    Pillow, pero decirlo a mano documenta que el formato tiene que quedar
    baseline (progresivo tiene un orden de bytes distinto y el panel no lo
    entiende) en vez de depender en silencio de que el default nunca cambie.
    """
    if rotate not in ROTATIONS:
        raise ValueError(f"invalid rotate {rotate!r}, expected one of "
                          f"{sorted(ROTATIONS)}")
    transpose = ROTATIONS[rotate]
    if transpose is not None:
        img = img.transpose(transpose)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=2, progressive=False)
    return buf.getvalue()
