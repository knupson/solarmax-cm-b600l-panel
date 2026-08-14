# Hardware

## The panel

A USB CDC device, `HL-VMAX-USB-Device`, VID_33C3 / PID_F101. The port is found by VID/PID, and
the geometry is parsed out of the serial number the panel returns, so neither is hardcoded. The
confirmed device is the 320x1480 panel in the Solarmax CM-B600L case.

### Protocol

```
open \\.\COM<n>                     CDC; the baud rate is irrelevant
TX  F0 A5 5A 0F                     handshake
RX  "VMAXA170320*1480S<serial>"     serial number, 26 ASCII bytes
TX  AA BB <brightness 0..100> CC DD
TX  <JPEG>                          one write per frame
```

A frame is a **raw baseline 4:2:0 JPEG** at the panel's exact size, with no header and no
framing: it starts with `FF D8 FF` and ends with `FF D9`.

The serial number carries the geometry: the three digits before the `*` are the width, the
digits after it the height. A serial that does not parse, or that yields a side under 100 px,
falls back to 320x1480 with a warning.

The panel is mounted upside down in the CM-B600L, so its profiles send frames rotated 180°.

Only one process can hold the port. The engine reconnects on its own, backing off 1, 2, 5 and
10 seconds.

## Sensors

Everything is read-only and needs no ring0 driver. Elevation is required for the GSA1 readings
and for NVMe SMART, which is why the autostart task runs elevated.

| Reading | Source |
|---|---|
| CPU load | PDH `% Processor Time`, via psutil |
| CPU clock | PDH `% Processor Performance` × the CPU's base clock |
| CPU model | `Win32_Processor` |
| CPU temperature | Gigabyte GSA1 ACPI-WMI, `ZFCGetCurrentTemp(id=2)` |
| VRM temperature | Same interface, `id=4` |
| VCore | Same interface, `EZVGetVoltage(Id=5)`, in mV |
| CPU package power | LibreHardwareMonitor, Intel RAPL |
| Per-core load, clock and temperature | LibreHardwareMonitor |
| Fan RPM | LibreHardwareMonitor, motherboard SuperIO (ITE IT8689E) |
| GPU load, temperature, hot spot, power, clock, fan | LibreHardwareMonitor |
| VRAM used | `GPU Memory Used`/`Total` where the card serves them, otherwise `D3D Dedicated Memory Used` over the adapter's `HardwareInformation.qwMemorySize` |
| SSD temperatures | LibreHardwareMonitor, NVMe SMART |
| RAM usage, network, volumes | psutil and CIM |
| RAM speed | `Win32_PhysicalMemory.ConfiguredClockSpeed`, falling back to `Speed` |
| Uptime, process count | CIM |

The readings that need PowerShell — GSA1, PDH and LibreHardwareMonitor — come from a sidecar
process running `vmaxpanel/sensors.ps1`, which the engine starts and restarts on its own.

VRAM is published only when the adapter's total is known, rather than computed against a guess.

### GSA1

Gigabyte's ACPI-WMI interface: namespace `root\WMI`, class `GSA1_ACPIMethod`, instance
`ACPI\PNP0C14\GSADEV0_0`.

> **Careful:** GSA1 also exposes `PIOWrite*`, `MEMWrite*` and `PCIWrite*` — arbitrary writes to
> I/O ports, physical memory and PCI configuration space. This driver uses **read methods only**.
> Do not add writes without knowing exactly which register they land on.

### Without the sensor DLLs

`LibreHardwareMonitorLib.dll` and `HidSharp.dll` are optional and not redistributed here. Without
them the panel draws clock, CPU load, CPU and VRM temperature, VCore, RAM, volumes with real
sizes, uptime, process count and network. What is unavailable is the GPU, per-core figures, disk
temperatures, fan RPM and package power. See [Installing](install.md#optional-sensors).
