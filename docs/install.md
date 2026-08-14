# Installing

At the end of this page the panel is drawing and its icon is in the Windows tray, where it
comes back at every logon.

## What the machine needs

| | |
|---|---|
| Windows | 10 or 11 |
| Python | 3.11 or newer, with `python.exe` on PATH |
| Board | Gigabyte with the GSA1 ACPI-WMI interface, for CPU temperature, VRM temperature and VCore. Everything else works on any board |
| Panel | Plugged in over USB. Windows shows it as `HL-VMAX-USB-Device` |
| LCD Control | Closed, and its `LCD ControlPowerBoot` autostart task disabled. Only one program at a time can drive the panel |

Python dependencies are `Pillow`, `pyserial` and `psutil`. They are installed for you below.

## The installer

Download the release, unzip it, then run `install.ps1` from **an administrator PowerShell**
in the folder it came in:

```powershell
.\install.ps1
```

It walks through the whole thing in order: finds Python, installs the dependencies, runs the
diagnostic, renders a test frame to `%TEMP%\vmaxpanel-install-preview.png`, asks before
registering the logon task, and starts the panel.

| Flag | What it does |
|---|---|
| `-Check` | Runs the checks and stops. Registers nothing, installs nothing |
| `-ProfilePath <file>` | The layout to install. Defaults to `vmaxpanel\profiles\apex.json` |
| `-NoAutostart` | Installs the dependencies but does not register the logon task |
| `-Yes` | Answers the confirmation automatically, for unattended runs |

Administrator rights are needed for the autostart step only: the task runs elevated, because
temperatures and disk health are unreadable without it. `-Check` and `-NoAutostart` run fine
in a normal console.

## By hand

The same steps, one at a time. The last one needs an administrator console.

```powershell
git clone https://github.com/knupson/solarmax-cm-b600l-panel
cd solarmax-cm-b600l-panel
pip install -r requirements.txt
python -m vmaxpanel --diagnose
python -m vmaxpanel --save preview.png
python -m vmaxpanel --profile vmaxpanel\profiles\apex.json --install
```

`--save` writes a PNG without touching the panel: it is exactly what the panel will show.

## Reading the diagnostic

`python -m vmaxpanel --diagnose` checks the dependencies, the sensor DLL, ffmpeg, the profile
and the panel, and marks each one:

| Mark | Meaning |
|---|---|
| `ok` | Present and working |
| `MISSING` | Blocks operation, and blocks installation until it is resolved |
| `optional` | Something is absent that limits the panel without breaking it |

An unplugged panel is `optional`: the engine keeps retrying until it appears. A port reported
as **in use** means another process holds it — the tray already running, or LCD Control.

## Optional sensors

Two third-party DLLs unlock the GPU, per-core figures, disk temperatures, fan RPM and package
power. They are not in the repo. Download LibreHardwareMonitor from
[its releases page](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases) and
copy **both** `LibreHardwareMonitorLib.dll` and `HidSharp.dll` into `vmaxpanel/lib/`.

Without them the panel still draws: clock, CPU load, CPU and VRM temperature, VCore, RAM,
disks with real sizes, uptime, process count and network.

Video backgrounds need **ffmpeg**, looked up in `vmaxpanel/lib/` and then on PATH:

```powershell
winget install Gyan.FFmpeg
```

Without it a video background falls back to a flat colour.

## Autostart

`--install` registers a scheduled task named **PanelVitals** that starts the tray at every
logon, elevated, with the profile given on the command line. Installing again replaces it, so
running it twice leaves a single task.

To start it without logging out:

```powershell
schtasks /Run /TN PanelVitals
```

## Done

The icon is in your tray, next to the clock. Windows hides new tray icons by default: click
the arrow to see it, and drag it out to keep it visible.

Right-click it for the menu — pause, brightness, profile, the editor. That is
[Using it](using-it.md).
