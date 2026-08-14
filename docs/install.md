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

Download the release and **unblock the zip before extracting it**: right-click it, Properties,
tick **Unblock**, OK. Windows marks anything downloaded from the internet, the mark is copied
to every extracted file, and a marked script is refused as unsigned.

Then right-click **`install.cmd`** and choose **Run as administrator**.

`install.cmd` is a wrapper around `install.ps1`. It exists because a `.ps1` cannot be run by
double-clicking -- Windows opens it in Notepad -- and because Windows refuses to run scripts
at all under its default execution policy. The wrapper bypasses that for its own process only
and changes nothing on the machine.

From a console it is the same thing, and the flags below work on either:

```powershell
.\install.cmd                 # or, if scripts are already allowed:
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

**Take 0.9.5 or newer, and copy every DLL in the release.** Two reasons:

- **Security.** Builds up to 0.9.3 reach CPU and motherboard sensors through **WinRing0**, on
  Microsoft's vulnerable-driver blocklist — arbitrary kernel read/write for any local process,
  certificate expired in 2008. From 0.9.5 that is gone and the access goes through **PawnIO**,
  which is signed and runs verified modules. Measured on 0.9.3: the driver loads on
  `Computer.Open()` *with every sensor category disabled*, so no setting avoids it — only a
  newer build does. PawnIO must be installed on the system
  (`winget install --id=namazso.PawnIO -e`); its modules travel inside the DLL.
- **It is no longer two files.** 0.9.3 needed `LibreHardwareMonitorLib.dll` and `HidSharp.dll`.
  0.9.6 ships 28 DLLs plus per-language subfolders, and a missing one makes `Open()` fail with
  nothing useful said. Copy the lot.

After copying the DLLs, check what you ended up with:

```powershell
python -m vmaxpanel --diagnose
```

The `ring0` line names which driver that DLL would load, and lists any such driver loaded right
now with the commands to remove it.

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
