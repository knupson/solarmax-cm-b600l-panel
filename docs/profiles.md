# Profiles

A profile is one JSON file in `vmaxpanel/profiles/`. It describes the whole layout: the panel
settings, the fonts, the background and every widget. Saving it reloads the panel live. A
profile that fails validation is not applied: the previous layout stays up and the error is
reported in the tray, in the editor and in `--status`.

Four ship with the repo: `apex.json`, `apex-es.json`, `embers.json` and `vitals.json`.

## The file

```json
{
  "version": 1,
  "name": "Apex",
  "designed_for": { "width": 320, "height": 1480 },
  "panel":   { "rotate": 180, "brightness": 100, "fps": 1, "jpeg_quality": 88 },
  "fonts":   { "hero": { "family": "Bahnschrift", "size": 66 } },
  "background": { "type": "solid", "color": "#0B0D12" },
  "widgets": [ ... ]
}
```

| Key | Value |
|---|---|
| `version` | `1` |
| `name` | The name shown in the tray and in the editor |
| `designed_for` | The size the layout was drawn for, in pixels |
| `panel` | Panel settings, below |
| `fonts` | Alias table, below |
| `background` | One background, below |
| `widgets` | The list of widgets, painted in order |

### `panel`

| Key | Range | Default |
|---|---|---|
| `rotate` | `0`, `90`, `180`, `270` | `0` |
| `brightness` | 0 to 100 | `100` |
| `fps` | 0.1 to 60 | `1.0` |
| `jpeg_quality` | 30 to 95 | `82` |

The panel refreshes at 60 Hz and discards anything above that. The shipped profiles run at 1 fps,
except Embers, which has an animated background and runs at 30.

The panel is mounted upside down in the CM-B600L case, which is why its profiles set
`"rotate": 180`.

### `fonts`

Each alias names a font **family** installed on the machine, not a file:

```json
"fonts": {
  "hero":  { "family": "Bahnschrift", "size": 66 },
  "dato":  { "family": "Franklin Gothic Medium Cond", "size": 21,
             "fallbacks": ["Bahnschrift Condensed", "Bahnschrift"] }
}
```

| Key | Value |
|---|---|
| `family` | Family name |
| `size` | Point size, a positive integer |
| `bold` | `true` picks the family's bold face. Default `false` |
| `fallbacks` | Families to try, in order, when `family` is not installed |

Widgets refer to an alias by name. The status reports which family a text was actually drawn
with when a fallback was used.

### `background`

| Type | Keys | Notes |
|---|---|---|
| `solid` | `color` | |
| `gradient` | `stops`, `angle` | `angle % 180` in [45,135) is vertical, the rest horizontal |
| `image` | `src`, `fit`, `color` | Static. `color` fills the letterbox |
| `procedural` | `name` (`scroll` or `pulse`), `stops`, `angle`, `speed`, `period` | Animated, built from the gradient |
| `sequence` | `src` (a folder), `fit`, `fps`, `color` | Animated, one image per frame |
| `video` | `src` (a file), `fit`, `fps`, `color` | mp4, webm, mkv, gif — whatever ffmpeg opens |

`stops` is a list of `{ "at": 0.0–1.0, "color": "#RRGGBB" }`, at least two of them. `fit` is
`cover`, `contain` or `stretch`. A background's `fps` is its own animation rate, independent of
`panel.fps`, and also tops out at 60.

`src` is always relative to `vmaxpanel/assets/`; paths that leave that directory are rejected.
The editor's **Choose…** button copies a file in for you.

Video backgrounds need ffmpeg. Without it, the background falls back to a flat colour and says
so.

## Widgets

Every widget has `id` (unique), `type`, `x` and `y`. Colours are `#RRGGBB`. `align` is `left`,
`center` or `right`.

| Type | Required | Optional |
|---|---|---|
| `text` | `metric`, `font`, `color`, `format` | `align`, `humanize`, `rules` |
| `label` | `text`, `font`, `color` | `align` |
| `bar` | `metric`, `w`, `h` | `radius`, `fill`, `track`, `min`, `max` |
| `arc` | `metric`, `r` | `thickness`, `start_angle`, `sweep`, `fill`, `track`, `min`, `max` |
| `graph` | `metric`, `w`, `h` | `color`, `track`, `samples`, `min`, `max` |
| `image` | `src`, `w`, `h` | |
| `rect` | `w`, `h` | `radius`, `fill`, `stroke`, `stroke_width` |

`min` and `max` bound the value a bar, arc or graph maps into its length; left out, they come
from the metric itself. `samples` is how many readings a graph plots, at least 1.

The order of the `widgets` list is the paint order. There is no `z` field: a filled `rect` placed
after a text covers it.

### `text`

```json
{ "id": "cpu-load", "type": "text", "x": 24, "y": 120, "metric": "cpu.load",
  "font": "big", "color": "#E8ECF4", "format": "{:.0f}%",
  "rules": [ { "when": "> 85", "color": "#E5484D" } ] }
```

`format` is a Python format string with exactly one unnamed field: `"{:.0f}%"`, `"{} MHz"`. When
the value is missing, the field becomes `--` and the rest of the template stays.

`humanize` formats a value in a way a template cannot:

| Mode | Input | Output |
|---|---|---|
| `none` | — | `format` is used |
| `rate` | bytes per second | `1.2 MB/s` |
| `bytes` | bytes | `3.0 GiB` |
| `duration` | seconds | `9h 11m` |

With `humanize` active, `format` must be `{}`.

`rules` recolour the value by comparison. Each is `{ "when": "> 85", "color": "#E5484D" }`, where
the operator is `>`, `>=`, `<` or `<=`. The first rule that matches wins; none matching leaves
`color`.

### `rect`

Dividers, frames and colour blocks.

```json
{ "id": "cpu-rule", "type": "rect", "x": 24, "y": 164, "w": 272, "h": 1, "fill": "#242834" }
{ "id": "frame", "type": "rect", "x": 14, "y": 540, "w": 292, "h": 320,
  "radius": 8, "stroke": "#242834", "stroke_width": 2 }
```

`fill` and `stroke` are each optional, but at least one has to be there. `w` and `h` are the real
size in pixels, so `"h": 1` is a one-pixel line; `bar` and `graph` draw one pixel larger than
written. `radius` is clamped to half the shorter side.

## Metrics

A widget names its reading by id. Ids not served on this machine draw dashes and are listed as
metrics with no data.

**A profile is shaped by the machine it was written on.** Volumes, network adapters, CPU cores
and disks are per-device: `vol.D.used` exists only where there is a D: drive, `core.6.temp`
only on a CPU with six cores. Of the four shipped profiles, **Embers reads nothing
device-specific and Vitals almost nothing**, while **Apex binds 22 of its 60 readings to three
volumes, three disks, six cores and an Ethernet adapter** — on a smaller machine that part of
the panel comes up blank.

To see where a profile stands on your machine before installing it:

```powershell
python -m vmaxpanel --profile <file> --diagnose
```

The `metrics` line names every id with no source here. It never blocks anything: a profile with
blanks still runs. Swap the widgets for ids your machine does serve, or start from Embers.

| Id | Reading | Unit |
|---|---|---|
| `cpu.name`, `cpu.name_short` | CPU model, full and trimmed | text |
| `cpu.load` | CPU load | % |
| `cpu.clock` | CPU clock | MHz |
| `cpu.temp` | CPU temperature | °C |
| `cpu.vrm_temp` | VRM temperature | °C |
| `cpu.vcore` | VCore | V |
| `cpu.power` | CPU package power | W |
| `cpu.fan` | CPU fan | RPM |
| `gpu.name` | GPU model | text |
| `gpu.load` | GPU load | % |
| `gpu.temp`, `gpu.hotspot` | GPU temperature, hot spot | °C |
| `gpu.clock` | GPU clock | MHz |
| `gpu.power` | GPU power | W |
| `gpu.vram` | VRAM used | % |
| `gpu.fan` | GPU fan | RPM |
| `mem.load` | RAM usage | % |
| `mem.used`, `mem.total` | RAM used and total | GiB |
| `mem.speed` | RAM speed | MT/s |
| `net.down`, `net.up` | Network, all adapters | B/s |
| `clock.time`, `clock.time_hms`, `clock.date` | Clock and date | text |
| `sys.uptime` | Uptime | s |
| `sys.procs` | Processes | count |

Some readings exist once per device, so their ids carry the instance:

| Pattern | Example | Reading |
|---|---|---|
| `core.N.load` / `.temp` / `.clock` | `core.3.temp` | Per-core figures |
| `vol.X.free` / `.used` / `.total` / `.load` | `vol.C.free` | Volume by drive letter |
| `disk.temp.N` | `disk.temp.0` | Disk temperature |
| `fan.N.rpm` | `fan.2.rpm` | Motherboard fan header |
| `mb.temp.N` | `mb.temp.1` | Motherboard temperature |
| `mb.temp.name.N` | `mb.temp.name.1` | What the board calls that sensor (text) |
| `net.<adapter>.down` / `.up` | `net.wi-fi.down` | One adapter |

The editor's metric selector lists what this machine actually serves, with the device's real
name. Where each reading comes from is in [Hardware](hardware.md).

## Beyond the profile

| What to change | Where |
|---|---|
| Position, format or colour of a value | The profile JSON |
| Text labels, separators, frames, colour blocks | `label` and `rect` widgets in the same file |
| Background | The profile's `background` block |
| Which metrics exist | `vmaxpanel/metrics.py` |
| Where a metric comes from | `vmaxpanel/providers/` |
| New sidecar sensors | `vmaxpanel/sensors.ps1` |
