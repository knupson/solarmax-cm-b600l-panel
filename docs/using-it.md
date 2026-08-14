# Using it

The panel is driven by the tray app. Everything below is a right-click away from its icon,
next to the clock.

## The tray menu

The first line is the status: `ok · 12043 frames` while it is drawing, or `Paused`, or
`Stopped`. Anything wrong is listed under it with a ⚠, up to four lines at a time.

| Item | What it does |
|---|---|
| **Pause (releases the port)** | Stops drawing and hands the panel over. Becomes **Resume** |
| **Restart the engine** | Reconnects to the panel and reloads the profile |
| **Brightness** | 25%, 50%, 75% or 100%. Applies immediately |
| **Profile** | Switches layout. The one in use is ticked |
| **Frames per second** | 1, 10, 30 or 60, each labelled with what it costs in CPU |
| **Layout editor…** | Opens the editor window |
| **Open the profile (JSON)** | Opens the current profile in the default text editor |
| **Export the profile…** | Writes a `.vmaxpanel` file into `perfiles-exportados/`, dated, and opens the folder |
| **View the log** | Opens the log file. Greyed out when there is none |
| **Exit** | Closes the tray and stops drawing |

Double-clicking the icon opens the editor as well. Only one editor window runs at a time.

**Profile** and **Frames per second** are greyed out while the editor is open: both write the
same file.

Pausing is how the panel is handed over to LCD Control without closing anything. If LCD Control
is started while the panel is drawing, the two fight over the port; the engine retries every few
seconds, but only one of them can hold it.

## The editor

Four tabs — **Widgets**, **Background**, **Fonts**, **Panel** — with the preview on the right
and **Save**, **Discard changes**, **Export…** and **Import…** along the bottom. The message
line under the buttons reports what happened and lists any layout errors.

Saving is atomic and the engine picks the file up on its own; there is no restart and no
"apply". A profile with errors is never saved.

### The preview

It is a working surface, not a thumbnail. Click a widget to select it, drag it to move it.

| Gesture | What it does |
|---|---|
| Wheel | Scrolls vertically. The panel is 1480 px tall, so this is the one used most |
| Shift+wheel | Scrolls horizontally |
| **Ctrl+wheel** | Zooms around the point under the cursor, from 5% to 400% |
| **Ctrl+0** | Back to fitting the window |
| Arrow keys | Nudge the selected widget by one pixel |
| **Ctrl+S** | Save |
| **Ctrl+Z** | Undo, up to 60 steps back |
| **Grid** checkbox | A 20/100 px grid over the frame. Off by default |

The grid and the selection outline are drawn over the frame and never into it, and neither is
written to the profile.

Animated backgrounds look frozen here: the preview is a single frame.

### The Widgets tab

The list on the left groups the layout's widgets; **+text**, **+label**, **+bar** and **+rect**
add one, **Delete** removes the selected one. The middle column holds its properties, plus
**Move** buttons that shift it by 1 or 10 px. A text widget also gets a **COLOUR BY VALUE**
block, where rules like `> 85` paint the value a different colour.

Every field is described in [Profiles](profiles.md).

### The Background tab

Pick the type and fill in its fields. For `image`, `sequence` and `video` the **Choose…** button
opens a file dialog and copies the chosen file into `vmaxpanel/assets/`, which is the only
directory a profile can read from. A hint under the type says what the type needs — for `video`
it also says whether ffmpeg was found.

### The Fonts and Panel tabs

**Fonts** lists the aliases the layout uses, with family, size, bold, and how many widgets use
each one. Fonts are requested by family name, not by file.

**Panel** holds rotation, brightness, frames per second and JPEG quality for this profile.

## Sharing a profile

A profile references assets and names fonts, so copying the bare `.json` is not enough. Export
packs the profile, its assets and a manifest into one `.vmaxpanel` file:

- From the tray: **Export the profile…**
- From the editor: **Export…** and **Import…**
- From the command line: [`--export` and `--import`](command-line.md#sharing-a-profile)

Fonts are not packaged. They are listed in the manifest, and importing reports which of them are
missing on this machine; the layout still draws, with substitutes.

Importing never overwrites an existing profile of the same name unless it is told to.

## Turning it off

**Exit** in the tray menu closes the app for this session. The scheduled task starts it again at
the next logon.

To bring the whole thing down, including the task and the sensor sidecar:

```powershell
python -m vmaxpanel --stop
```

To stop it starting at logon for good:

```powershell
python -m vmaxpanel --uninstall
```

Both are in the [command-line reference](command-line.md), along with `--status`, which answers
"is it drawing right now?" from a console or a script.
