# Solarmax Display — instrucciones del proyecto

Driver propio para el panel del gabinete **Solarmax CM-B600L** (320x1480, `HL-VMAX-USB-Device`,
VID_33C3/PID_F101, **COM3**). Reemplaza al software vendor *LCD Control*.

**Leer `README.md` antes de tocar cualquier cosa.** Tiene el protocolo serial completo, el
mapa de sensores, la causa raíz documentada y la guía de edición.

## Lo que no hay que reinvestigar

- **`CpuUsage`/`CpuUse` de LCD Control son `% Processor Utility`** = carga × clock/2500,
  clampeado a 100 → satura con carga real ≥61%. Verificado desde adentro de la app y desde
  afuera en el mismo segundo. No existe token con la carga time-based; ya probé ~38 nombres.
- **WinRing0 (driver ring0 de LibreHardwareMonitor) está bloqueado**: `StartService → 0xE1`,
  está en la blocklist de drivers vulnerables de Windows. Sin MSR. No intentar habilitarlo.
- Por eso **package power (W) y fan RPM de CPU no se pueden leer**. Esos slots del layout
  son ahora VCORE y VRM. No prometer que vuelven.
- `ECLReadByte/Word` de GSA1 **no está implementado** en la B760M D3HP.
- El panel está montado al revés: los frames van **rotados 180°**.
- `consola.ttf`/`consolab.ttf` son **Consolas, de Microsoft**: no se redistribuyen. Las
  fuentes se piden por familia; en cualquier Windows están.
- `daemon/assets/back.png` es arte del tema Vitals de **LCD Control**: no se redistribuye.
  El fondo del perfil propio (`vmaxpanel/profiles/vitals.json`) es un `gradient`.

## Operación

```powershell
cd E:\Claude\Solarmax_Display\daemon
.\start.ps1            # idempotente; -Force reinicia
.\stop.ps1             # mata daemon + sidecar y barre huérfanos
python panel.py --save preview.png     # previsualizar sin tocar el panel
```

**Trampa recurrente:** un `powershell.exe` corriendo `sensors.ps1` que sobrevive al daemon
se queda con `LibreHardwareMonitorLib.dll` tomado y bloquea mover o borrar el directorio.
Filtrar procesos por `StartTime` no alcanza — usar `stop.ps1`, que barre por línea de
comandos.

## Seguridad

Los sensores salen de la interfaz **GSA1 ACPI-WMI** de Gigabyte (`root\WMI`,
`GSA1_ACPIMethod`), que además de lecturas expone `PIOWrite*`, `MEMWrite*`, `PCIWrite*`:
escritura arbitraria a puertos I/O, memoria física y espacio PCI. **El daemon usa solo
métodos de lectura.** No agregar escrituras sin saber exactamente a qué registro van.

## Estado

- Daemon funcionando, arrancado a mano.
- **Autostart pendiente**: la tarea `PanelVitals` (ver README, sección Autostart) todavía no
  está registrada — hizo falta permiso del usuario y quedó sin hacer.
