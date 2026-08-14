# Command line

Everything the tray app does can also be driven from a console. This page is the full
reference, for development and for troubleshooting. Day-to-day use needs none of it — see
[Using it](using-it.md).

Run these from the repository root.

## Entry points

| Command | What it runs |
|---|---|
| `python -m vmaxpanel.tray` | The tray app: icon, menu, and it supervises the engine |
| `python -m vmaxpanel` | The engine alone, in the foreground |
| `python -m vmaxpanel.editor` | The layout editor |

`vmaxpanel.tray` accepts `--profile`, `--port` and `--log`. `vmaxpanel.editor` accepts
`--profile`. `python -m vmaxpanel` accepts everything below.

Under `pythonw.exe` there is no console, so `--log` is the only place output goes.

## Flags

Every flag also accepts its original Spanish name; the two spellings are the same flag.

| Flag | Alias | What it does |
|---|---|---|
| `--profile <file>` | | The layout to use. Defaults to `vmaxpanel/profiles/vitals.json` |
| `--port <COMn>` | | The panel's port. Autodetected by VID/PID when absent |
| `--save <file.png>` | | Renders one frame to a PNG and exits, without touching the panel |
| `--once` | | Sends a single frame and exits |
| `--no-sensors` | | Does not launch the sensor sidecar |
| `--log <file>` | | Writes everything to this file as well as to the console |
| `--diagnose` | `--diagnostico` | Checks dependencies, sensors, profile and panel, then exits |
| `--install` | `--instalar` | Checks, then registers the logon task |
| `--uninstall` | `--desinstalar` | Removes the logon task |
| `--stop` | `--parar` | Brings everything down now: task, tray, engine and sidecar |
| `--status` | `--estado` | Says whether the panel is drawing right now |
| `--export <file>` | `--exportar` | Packs the profile and its assets into one file |
| `--import <file>` | `--importar` | Installs a profile that was exported |
| `--if-exists <mode>` | `--si-existe` | On import, when that name already exists: `fail`, `rename` or `overwrite` |

## Rendering without the panel

```powershell
python -m vmaxpanel --save preview.png
python -m vmaxpanel --profile vmaxpanel\profiles\embers.json --save preview.png
python -m vmaxpanel --save preview.png --no-sensors
```

The PNG is exactly what the panel receives. `--no-sensors` skips the sidecar and renders from
psutil and CIM alone: the clock, CPU load, RAM, network and volumes are still real, and the
readings that come from GSA1 or LibreHardwareMonitor draw dashes.

## Is it drawing?

```powershell
python -m vmaxpanel --status
```

```
drawing - profile Apex, panel ok, 12043 frames, 1 fps
published 2 s ago (pid 23060)
```

Exit code **0** when it is drawing, **1** when it is not. The first line reads `drawing`,
`PAUSED (the port is free)` or `STOPPED`, and any problems are listed underneath.

The process driving the panel publishes this to `vmaxpanel-estado.json` every 5 seconds. The
age is always reported, and a reading more than 30 seconds old is called out: a process can be
alive and no longer publishing. If the process that wrote the file is gone, that is said first.

## Installing and removing

```powershell
python -m vmaxpanel --diagnose
python -m vmaxpanel --profile vmaxpanel\profiles\apex.json --install
python -m vmaxpanel --uninstall
python -m vmaxpanel --stop
```

`--diagnose` exits 0 when nothing blocks and 2 when something does. `--install` needs an
administrator console: the task it registers runs elevated. `--install` refuses to register
anything while a check says MISSING.

`--stop` stops the scheduled task, kills the tray and the engine, and kills the sensor sidecar.
It recognises its own processes by command line, not by image name. A process it cannot inspect
or kill is reported, with the suggestion to try again from an administrator console.

`schtasks /Run /TN PanelVitals` starts it again.

## Sharing a profile

```powershell
python -m vmaxpanel --profile vmaxpanel\profiles\apex.json --export my-profile.vmaxpanel
python -m vmaxpanel --import my-profile.vmaxpanel
python -m vmaxpanel --import other.vmaxpanel --if-exists rename
```

A `.vmaxpanel` file is a zip holding the profile, whatever assets its background references, and
a manifest. Export refuses to overwrite an existing file. Import defaults to `fail` when a
profile of that name already exists; `rename` and `overwrite` are the alternatives.

Fonts are not packaged. They are listed in the manifest, and import reports which of them are
missing on this machine.

An imported bundle is validated before anything is written, members with absolute paths, `..` or
a drive letter are rejected, and extraction is bounded by the declared size.

## Troubleshooting

| Symptom | Command |
|---|---|
| Nothing is on the panel | `python -m vmaxpanel --status` |
| It will not start | `python -m vmaxpanel --diagnose` |
| A sensor reads `--` | `python -m vmaxpanel --diagnose`, then check the sensor DLLs |
| The layout looks wrong | `python -m vmaxpanel --save preview.png` |
| Something is holding the port | `python -m vmaxpanel --stop`, then start it again |
| It failed at logon | Read the log the task writes, `vmaxpanel.log` |

Testing the task without rebooting:

```powershell
Stop-ScheduledTask PanelVitals
Start-ScheduledTask PanelVitals
python -m vmaxpanel --status
```

Only one process at a time can hold the panel, so a manually started engine has to be closed
before starting the task.

## Tests

```powershell
pip install pytest
python -m pytest -q
```

639 tests, no hardware required. A test that runs longer than 60 seconds is killed and the stack
of every thread is written to `pytest-hang.txt` (`VMAXPANEL_TEST_TIMEOUT` changes the limit).
