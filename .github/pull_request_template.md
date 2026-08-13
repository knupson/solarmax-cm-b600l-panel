<!--
Al mandar este PR aceptás el CLA de CONTRIBUTING.md: le cedés al dueño del
proyecto un derecho no exclusivo para usar y relicenciar tu aporte, y seguís
siendo dueño de lo tuyo.
-->

## Qué cambia y por qué

<!-- El problema concreto, no el nombre del cambio. Si arregla un bug, cuál era
     la causa: eso es lo que evita que vuelva. -->

## Cómo se verificó

- [ ] `python -m pytest` pasa entero
- [ ] El test del cambio **falla sin el fix** (si es un arreglo)
- [ ] Miré el resultado, no sólo los tests: `python -m vmaxpanel --save preview.png`

<!-- Si es visual, pegá el antes y el después. Tres bugs sobrevivieron 590 tests
     verdes en este repo hasta que alguien miró un PNG. -->

## Chequeos

- [ ] Sin dependencias nuevas de Python
- [ ] `daemon/` sin tocar
- [ ] Sin fuentes ni DLL de terceros committeadas
- [ ] Si suma un fondo o algo que abre un proceso o archivo, se cierra en `close()`
