"""The render loop.

Separate cadences: sensors are sampled every `sample_period` (1 s by default) and
frames go out at the layout's fps. With animated backgrounds that separation is
what allows a background at 10 fps with data at 1 Hz, without re-reading sensors
ten times a second.

The transport is injected (`link_factory`), so the whole loop is tested against
FakeTransport, with no panel plugged in.
"""
import sys
import time
from dataclasses import dataclass

from .render.renderer import History, Renderer, to_jpeg
from .transport.panel_link import PanelNotFound


@dataclass
class EngineConfig:
    profile_path: str
    sample_period: float = 1.0
    reconnect_backoff: tuple = (1.0, 2.0, 5.0, 10.0)
    max_iterations: int | None = None       # None = forever; the tests bound it
    history_len: int = 320


class Engine:
    def __init__(self, store, registry, config, link_factory, clock=time):
        self.store = store
        self.registry = registry
        self.cfg = config
        self._link_factory = link_factory
        self._clock = clock
        self._stop = False
        self._link = None
        self._renderer = None
        self._history = History(config.history_len)
        self._sample = {}
        # None, not 0.0: it distinguishes "never sampled yet" from "sampled at
        # instant 0.0". If the truthiness of self._sample were used for that (as
        # an earlier version of this loop did), a registry returning {} -- no
        # providers, or every metric UNAVAILABLE and none registered -- never
        # becomes truthy and the cadence guard (`self._sample and ...`) never
        # fires: every frame would re-read the registry, which is exactly the 10x
        # re-read this cadence split exists to avoid.
        self._last_sample_at = None
        self._last_error = None
        self._rechazo_avisado = None
        self.stats = {"frames": 0, "reconnects": 0}

    # --- lifecycle ---

    def stop(self):
        self._stop = True

    def state(self) -> dict:
        layout = self.store.current
        return {
            "panel": "ok" if self._link is not None else "disconnected",
            "profile": layout.name if layout else None,
            "sn": self._link.serial_number if self._link else None,
            "fps": layout.panel.fps if layout else None,
            "resolution": self.registry.resolution(),
            "unavailable": self._sin_datos(layout),
            "warnings": (self._renderer.warnings() if self._renderer else []) + self.store.errors,
            "frames": self.stats["frames"],
            "last_error": self._last_error,
        }

    def _sin_datos(self, layout) -> dict:
        """Metrics with no data: the ones the Registry knows about, plus the ones
        the LAYOUT uses and nobody serves.

        Family metrics (`fan.9.rpm`, `core.12.temp`, `vol.Z.free`) cannot be
        enumerated -- they are a pattern, not a list -- so the Registry cannot
        report them on its own. The only thing that knows which ones are really in
        use is this loop, which has the layout in front of it. Without this, a
        profile asking for a metric this machine does not have draws dashes while
        the status says nothing is missing: exactly the kind of lying status this
        project exists not to commit.
        """
        faltan = dict(self.registry.unavailable())
        if layout is None:
            return faltan
        servidas = self.registry.resolution()
        for w in layout.widgets:
            mid = getattr(w, "metric", None)
            if mid and mid not in servidas:
                faltan.setdefault(mid, "the profile uses it and no provider on this "
                                       "machine serves it")
        return faltan

    def run(self):
        attempt = 0
        try:
            while not self._done():
                try:
                    self._connect()
                    attempt = 0
                    self._serve()
                except (OSError, PanelNotFound) as e:
                    self._last_error = str(e)
                    self._drop_link()
                    if self._done():
                        break
                    self.stats["reconnects"] += 1
                    delay = self.cfg.reconnect_backoff[
                        min(attempt, len(self.cfg.reconnect_backoff) - 1)]
                    self._clock.sleep(delay)
                    attempt += 1
        finally:
            # Either a clean exit (stop() or max_iterations) or an exception
            # escaping the whole loop (a programming ValueError/RuntimeError --
            # deliberately not caught above, see _render_once): in both cases
            # run() is never going to write to the panel again, so the transport
            # has to be closed AND forgotten -- going through _drop_link() rather
            # than a bare close(). A close() that left self._link set would leave
            # state()["panel"] returning "ok" for a demonstrably closed link
            # (dead.closed / made[0].closed in the tests below), which is the same
            # class of lying status this project exists to avoid: LCD Control
            # reported a CPU load that was not the real one, and a
            # state()["panel"] == "ok" for a closed port is a status field lying
            # for the same reason, only in a different field. This field's
            # contract is binary -- "ok" or "disconnected", with no third "was
            # connected but is not any more" state -- so "disconnected" is the
            # only honest answer once run() has finished, whatever it finished
            # for.
            self._drop_link()

    def _done(self):
        if self._stop:
            return True
        limit = self.cfg.max_iterations
        return limit is not None and self.stats["frames"] >= limit

    # --- connection ---

    def _connect(self):
        if self._link is not None:
            return
        # The layout check comes BEFORE opening the transport: the other way
        # around (open first, check afterwards) an invalid layout would leave a
        # freshly opened transport unclosed -- _drop_link() only closes
        # self._link, and self._link would not have been assigned yet on that
        # branch, so the descriptor/port would leak on every reconnection
        # attempt. Checking first also avoids really opening the port for a
        # problem that has nothing to do with the transport.
        layout = self.store.current
        if layout is None:
            # Re-read HERE and not only in _serve(): _serve() runs after
            # connecting, so an engine started without a valid layout never got
            # to look at the file again and spun in the backoff forever, even
            # after the user fixed the JSON. The tray starts before the profile
            # is guaranteed, so this is the ordinary path.
            self.store.reload_if_changed()
            layout = self.store.current
        if layout is None:
            errs = "; ".join(self.store.errors) or "no valid layout is loaded"
            raise OSError(errs)
        link = self._link_factory()
        try:
            link.open()
            link.set_brightness(layout.panel.brightness)
        except Exception:
            # link_factory() can return a transport that is already really open
            # (SerialTransport opens the serial port in its own __init__, before
            # open() sends the handshake). If the handshake or the initial
            # brightness fail here, self._link never gets assigned -- so
            # _drop_link(), in run()'s except, has nothing to close and the
            # freshly opened handle would leak on every reconnection attempt,
            # trusting the garbage collector to close it. Same "open handle
            # holding the resource" pattern this project already has documented
            # for sensors.ps1 / LibreHardwareMonitorLib.dll, applied now to the
            # panel's COM port. The exception is re-raised unmodified: run()'s
            # backoff still sees it the same way.
            try:
                link.close()
            except Exception:
                pass
            raise
        self._link = link
        self._renderer = Renderer(layout, panel_size=link.geometry)

    def _drop_link(self):
        if self._link is not None:
            try:
                self._link.close()
            except Exception:
                pass
        # The renderer is closed too, not merely forgotten: it owns the
        # background, and a video background has an ffmpeg behind it. Dropping
        # the reference without closing would leave a decoder running per
        # reconnection, waiting for the garbage collector to clean it up -- the
        # same orphan-process pattern that already happened with the sensor
        # sidecar.
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception:
                pass
        self._link = None
        self._renderer = None

    # --- the loop ---

    def _serve(self):
        while not self._done():
            t0 = self._clock.time()
            self._render_once()
            period = 1.0 / max(0.1, self.store.current.panel.fps)
            if self._done():
                return
            # max(0.0, ...): a frame that takes longer than its period (a slow
            # render, or a badly measured t0 above) must not turn into a negative
            # sleep. The lag is not accumulated to "catch up" with longer sleeps
            # afterwards -- that would chain one slow frame's delay onto every
            # frame after it. It is dropped instead: the next frame starts as soon
            # as this one ends, with no extra penalty and no attempt to recover
            # the lost time.
            self._clock.sleep(max(0.0, period - (self._clock.time() - t0)))

    def _render_once(self):
        self._refresh_layout()
        self._refresh_sample()
        layout = self.store.current
        img = self._renderer.frame(self._sample, self._history.series())
        rotate = layout.panel.rotate
        problem = self._rotation_problem(rotate)
        if problem is not None:
            # Nothing is sent: the panel accepts a frame of the wrong shape
            # without complaint and paints it as garbage. No exception is raised
            # either -- reconnecting does not fix a configuration error, and
            # killing the loop would stop the corrected profile from being hot
            # reloaded. It is recorded, and the loop keeps spinning at the
            # layout's fps until somebody fixes the rotate.
            self._last_error = problem
            return
        self._link.send_frame(to_jpeg(img, rotate, layout.panel.jpeg_quality))
        self.stats["frames"] += 1

    def _rotation_problem(self, rotate) -> str | None:
        """Why this `rotate` does not fit this panel, or None.

        The layout validator cannot decide this: it does not know the panel's
        geometry, and a layout designed 1480x320 with rotate 90 is perfectly valid
        for a 320x1480 panel. Here both are known -- the canvas is fixed by
        `panel_size=link.geometry` in _connect() -- so 90/270 only get through if
        the panel is square.
        """
        if rotate not in (90, 270):
            return None
        g = self._link.geometry
        if g.width == g.height:
            return None
        return (f"panel.rotate {rotate} turns the frame into {g.height}x{g.width}, "
                f"but the panel is {g.width}x{g.height}: use 0 or 180")

    def _refresh_layout(self):
        changed, errors = self.store.reload_if_changed()
        if errors:
            self._reportar_rechazo(errors)
        elif changed:
            self._rechazo_avisado = None
        if changed and self._renderer is not None:
            layout = self.store.current
            self._renderer.set_layout(layout)
            self._link.set_brightness(layout.panel.brightness)

    def _reportar_rechazo(self, errors):
        """Reports that a profile was rejected, once per distinct content.

        The invariant "a broken JSON does not blank the panel" had a hidden cost:
        a rejected profile was COMPLETELY silent. The engine kept drawing the
        previous layout and left no trace anywhere, so from the outside it looks
        like "I edited the profile and the panel did not change". It happened
        twice with the user watching the panel, both times for the same reason: a
        new metric the live process does not know about, because the code changed
        after it started.

        Once per distinct error content: at 30 fps, one warning per frame is 1800
        lines a minute. It resets when a good layout comes in, so the next
        rejection warns again.
        """
        firma = tuple(errors)
        if firma == getattr(self, "_rechazo_avisado", None):
            return
        self._rechazo_avisado = firma
        print(f"profile rejected, keeping the previous one: {'; '.join(errors)}",
              file=sys.stderr)
        print("  if you just added a metric, this process started earlier and does "
              "not know it: the tray has to be restarted.", file=sys.stderr)

    def _refresh_sample(self):
        now = self._clock.time()
        if self._last_sample_at is not None and now - self._last_sample_at < self.cfg.sample_period:
            return
        self._sample = self.registry.read()
        self._history.push(self._sample)
        self._last_sample_at = now
