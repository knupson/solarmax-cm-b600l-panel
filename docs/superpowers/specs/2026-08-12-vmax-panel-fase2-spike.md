# Fase 2 — spike de throughput

**Fecha:** 2026-08-12
**Herramienta:** `research/spike_throughput.py`
**Pregunta:** cuántos fps traga el panel, porque eso decide todo el diseño de los fondos animados.

## Resultado corto

**Nada del lado del host es el cuello.** A 10 fps sobra entre 5 y 7 veces en cada eje medido.
El límite real del panel **no se puede medir desde el host** y hace falta una cámara.

## Medido

Perfil Vitals real (320x1480), muestra fija, sin sensores. Máquina: i5-12400F.

| Etapa | Costo por frame | Techo |
|---|---|---|
| `Renderer.frame()` | 3,8 ms (p95 5,2) | ~260 fps |
| `to_jpeg()` q82 (41,4 KB) | 1,8 ms (p95 2,4) | ~550 fps |
| Write serial de esos bytes | 4,4 ms (p95 5,8) | ~227 fps |
| **Total extremo a extremo** | **10,0 ms** | **100 fps** |

Throughput de escritura sostenido: **9,2–9,7 MB/s**. Bajar la calidad JPEG ayuda poco: q40
(24,8 KB) da 126 fps contra 95 de q82, porque el write ya no es el problema.

### Costo de un fondo que cambia por frame

Hoy el fondo se construye una vez y se cachea; animarlo es exactamente lo que fase 2 agrega.

| Estrategia | Costo por frame | Techo solo por el fondo |
|---|---|---|
| Gradiente reconstruido | 7,7 ms | 130 fps |
| Secuencia PNG (decode + resize) | 2,7 ms | 367 fps |
| Secuencia JPEG (decode + resize) | 2,8 ms | 351 fps |
| Procedural (scroll afín en PIL) | 0,5 ms | 1863 fps |

A 10 fps, una secuencia de imágenes suma 2,8 ms sobre los 10 ms actuales: **13 ms, o 77 fps de
techo.** Ni cerca de apretar.

## El límite del panel quedó sin medir, y es a propósito

500 frames sostenidos (20 MB en 5 s) y el ritmo de escritura se mantuvo **plano en ~227 fps por
quinto de la corrida**: 229 · 225 · 229 · 227 · 231. El panel **nunca frenó al host**.

Un LCD de este tipo no dibuja 227 fps. Que no haya contrapresión significa que el firmware (o el
driver CDC) **acepta y descarta** lo que no alcanza a mostrar, en vez de bloquear la escritura.
Conclusión honesta: desde el host no hay ninguna señal de la que deducir el refresco real.

**Lo que falta para saberlo:** filmar el panel con un celular a 60 fps mostrando un contador que
cambie cada frame, y contar cuántos valores distintos aparecen por segundo. Es la única medición
que queda y necesita a alguien delante del gabinete.

## Implicancias para el diseño de fase 2

1. **El fps es un parámetro del perfil, no una restricción técnica.** `panel.fps` ya existe y
   valida hasta 30. Se puede subir sin tocar nada.
2. **Descartar frames en el host no tiene sentido todavía.** Con 10 ms por frame y sin
   contrapresión, no hay cola que administrar. Si la medición con cámara diera un refresco bajo
   (p.ej. 15 fps), ahí sí conviene limitar en el perfil para no quemar CPU al vacío.
3. **Las secuencias de imágenes son la opción obvia para empezar:** más baratas que reconstruir
   un gradiente, sin dependencias nuevas, y el `background.type = "sequence"` ya está reservado
   en el validador.
4. **El video queda para el final y con una decisión de dependencia adelante:** no hay decoder en
   la stdlib. Contra el criterio de "nada no-redistribuible ni pesado", conviene dejarlo afuera o
   resolverlo convirtiendo a secuencia en tiempo de importación.
5. **Procedural es prácticamente gratis** (0,5 ms) y no necesita assets, así que es el mejor
   candidato para los fondos que se reparten con la app.
