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

Everything is read-only. **The optional sensor DLL is the exception worth knowing about:**
LibreHardwareMonitor reads CPU package power and per-core figures through MSRs, and builds up
to 0.9.3 load **WinRing0** to get there -- a driver on the Windows vulnerable-driver blocklist.
Use **0.9.5 or newer**, where that is replaced by PawnIO — signed, running verified modules
that ship inside the DLL. `python -m vmaxpanel --diagnose` says which one you have and whether
such a driver is loaded right now. Everything else here needs no kernel driver. Elevation is required for the GSA1 readings
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
| Motherboard temperatures | Same SuperIO. `mb.temp.N` is the chip's input N, and `mb.temp.name.N` is what that input is called — see below |
| GPU load, temperature, hot spot, power, clock, fan | LibreHardwareMonitor |
| VRAM used | `GPU Memory Used`/`Total` where the card serves them, otherwise `D3D Dedicated Memory Used` over the adapter's `HardwareInformation.qwMemorySize` |
| SSD temperatures | LibreHardwareMonitor, NVMe SMART |
| RAM usage, network, volumes | psutil and CIM |
| RAM speed | `Win32_PhysicalMemory.ConfiguredClockSpeed`, falling back to `Speed` |
| Uptime, process count | CIM |

The readings that need PowerShell — GSA1, PDH and LibreHardwareMonitor — come from a sidecar
process running `vmaxpanel/sensors.ps1`, which the engine starts and restarts on its own.

VRAM is published only when the adapter's total is known, rather than computed against a guess.

## Naming the motherboard temperatures

The SuperIO has six temperature inputs, and which physical point is wired to each one is a
board-layout decision: `mb.temp.1` means something different on a different board. So a profile
should not label these by hand. `mb.temp.name.N` carries what the input is *called*, and the
shipped profiles bind the column label to that metric — the panel then names the column with
whatever the machine it is running on reports.

The name comes from LibreHardwareMonitor, which has a board-specific mapping for the 332 boards it
knows (`SuperIOHardware.GetBoardSpecificConfiguration`). On a board it does not know, every input
arrives as `Temperature #N`, and `_NOMBRES_POR_PLACA` in `providers/sidecar_providers.py` fills it
in — keyed by the SMBIOS board model, and only over that generic name, so a name the library
supplied always wins.

Adding a board to that table means establishing the mapping, not copying a list from somewhere.
Nothing on the machine will tell you: the Gigabyte GSA1 interface exposes readings by numeric id
and has no method returning a label for one, `Win32_TemperatureProbe` reports "LM78A" with an empty
reading, and the ACPI thermal zones are unrelated. What does work is identifying them by how they
behave, which needs nothing installed:

| Load | The input that… | Is |
|---|---|---|
| CPU | follows package power within seconds, and drops the moment the load stops | the CPU socket |
| CPU | rises slowly and decays slowly | the VRM |
| GPU | rises clearly while the other flat ones only drift with the case air | the one at the PCIe slot |
| either | stays flat | a system/ambient point |

On the B760M D3HP that gives inputs 0–5 as System1, PCH, CPU, PCIEX16, VRM MOS and System2. The
four identified by behaviour match the names HWiNFO shows, which is an independent source; the two
ambient ones cannot be told apart from each other, so which is System1 and which is System2 rests
on the order the chip reports them in.

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
