# Contributing to the Solarmax CM-B600L panel driver

Contributions are welcome. This explains how the project is put together, what is expected of
a change, and what **not** to touch.

> The code comments, the app's messages and the test names are still in Spanish. Translating
> them is in progress. Until a file has been translated, follow the language of the file you
> are editing.

## Contributor agreement (CLA)

By opening a pull request you agree that:

1. The contribution is yours, or you have the right to license it.
2. You grant the project's owner (Alejandro, [@knupson](https://github.com/knupson)) a
   **non-exclusive, worldwide, perpetual, irrevocable, royalty-free** copyright licence to use,
   modify, publish and relicense your contribution as part of this project.
3. You keep ownership of your contribution and can use it anywhere else you like.

This exists so the project can keep a single coherent licence, and so the owner can relicense
later without having to track down everyone who ever sent a patch. It does not ask for
exclusivity and it takes nothing away from you.

Code you contribute is covered by the [PolyForm Noncommercial 1.0.0](LICENSE) licence, like the
rest of the project.

## Getting started

```powershell
git clone https://github.com/knupson/solarmax-cm-b600l-panel
cd solarmax-cm-b600l-panel
pip install -r requirements.txt
pip install pytest
python -m pytest                          # 594 tests, about 85 s
python -m vmaxpanel --diagnostico         # what is missing on this machine
python -m vmaxpanel --save preview.png    # renders without touching the panel
```

**You do not need the panel to develop.** `--save` draws to a PNG and the tests never touch
hardware, so they run on any Windows machine. What *is* machine-specific is some of the
sensors, and the diagnostic tells you which.

You do not need the LibreHardwareMonitor DLLs either: without them you lose the GPU, disk
temperatures and fan RPM, and everything else works.

Two tests need ffmpeg installed, because they check the text ffmpeg writes to stderr. Without
it they skip themselves.

## The map

```
vmaxpanel/
  cli.py          arguments: --save / --once / --estado / --instalar / --parar
  app.py          wires the engine up with its dependencies
  engine.py       the loop: read sensors -> render -> send to the panel
  tray.py         tray app (pure ctypes/Win32)
  editor.py       layout editor (pure Tkinter)
  metrics.py      the metric catalogue: id, name, unit, range
  layout/
    schema.py     validates the profile JSON and explains every error
    model.py      the layout dataclasses
    loader.py     loading and hot reload (by content hash)
  render/
    renderer.py   composes the frame, scales the layout to the real panel
    widgets.py    text, label, bar, arc, graph, image, rect
    background.py solid, gradient, image, sequence, procedural, video
    video.py      ffmpeg as an external process
    fonts.py      resolves families and fallback chains
  providers/
    registry.py   resolves each metric to the highest-priority available provider
    sidecar.py    talks to sensors.ps1
    ...
  transport/
    panel_link.py the serial protocol, autodetection by VID/PID
  sensors.ps1     PowerShell sidecar: GSA1, PDH, LibreHardwareMonitor, SMBIOS
```

## Adding things

**A new widget.** A dataclass in `layout/model.py`, its entry in `WIDGET_TYPES` in
`layout/schema.py`, and a `_draw_*` function in `render/widgets.py`. Tests go in
`tests/test_widgets.py`: draw onto a canvas and assert on specific pixels, not on "it did not
crash".

**A new metric.** An entry in `metrics.py` (id, name, unit, minimum, maximum) and some provider
that serves it. If it comes from PowerShell, it goes in `sensors.ps1` and in
`providers/sidecar_providers.py`. The preference order between providers is `PROVIDER_PRIORITY`
in `providers/registry.py`.

**A new background.** `BACKGROUND_TYPES` in `layout/schema.py` and a branch in
`render/background.py`. If it is animated, add it to `ANIMADOS`. If it opens a process or a
file, it must close it in `close()`: every animated background that does not close leaves an
orphan process behind on every profile save.

## What is expected of a change

- **A failing test first.** The project was built that way and it shows: the bugs that reached
  users are exactly the ones no test was looking at. If you fix something, the test has to fail
  before the fix.
- **Look at the result, not just the test.** Four visible defects survived 590 green tests
  until someone rendered a PNG and looked at it. `--save` is cheap.
- **No new dependencies.** There are three (`Pillow`, `pyserial`, `psutil`) and the idea is that
  there stay three. The tray is ctypes and the editor is Tkinter for exactly that reason.
- **Comments that say why, not what.** The repo is full of comments explaining the trap that
  motivated each line. That is deliberate: they are the reason a given bug does not come back.

## What not to touch

- **`daemon/`** is the byte-identical rollback path for the previous version. A `git diff`
  against its base commit has to stay empty. If you think you need to change something in
  there, you do not.
- **`daemon/stop.ps1` does not work for the new engine** and is not fixed, for the reason
  above. To bring the panel down: `python -m vmaxpanel --parar`.
- **WinRing0 and any ring0 driver.** It is on the Windows vulnerable-driver blocklist and it is
  not going to be enabled. If a metric needs MSR access, it does not get read.
- **GSA1 writes.** Gigabyte's WMI interface exposes `PIOWrite`, `MEMWrite` and `PCIWrite`:
  arbitrary writes to I/O ports, physical memory and PCI space. This project uses **read
  methods only** and it stays that way.
- **Third-party fonts and DLLs.** They are not committed. Fonts are requested by family with a
  fallback chain; the DLLs are downloaded by the user.

## Reporting a bug

Open an issue with the output of `python -m vmaxpanel --diagnostico` and, if it concerns what
gets drawn, the PNG from `python -m vmaxpanel --save bug.png`. Those two are almost always
enough.

If something hangs, `tests/conftest.py` kills any test running longer than 60 s and writes every
thread's stack to `pytest-hang.txt`. That file is what tells you what hung.
