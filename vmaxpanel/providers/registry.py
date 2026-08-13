"""Resolves each metric id to the highest-priority available provider."""
from ..metrics import UNAVAILABLE, group_for, is_metric, spec_for
from .base import Provider

# Most specific first: if a Gigabyte board serves cpu.temp through GSA1, that
# beats LibreHardwareMonitor's generic reading.
# gsa1 before cpulhm: the CPU temperature through GSA1 is the board sensor, closer
# to what the BIOS reports than the average of the cores.
PROVIDER_PRIORITY = ["gsa1", "cpulhm", "mobo", "msr", "pdh", "lhm",
                     "smbios", "wmi", "psutil"]

_NO_PROVIDER = "no provider on this machine serves this metric"


class Registry:
    def __init__(self, providers: list[Provider]):
        # metrics() is read once, here. A provider whose metrics() is dynamic (e.g.
        # LhmProvider.served, which discovers disk.temp.N from the sidecar's first
        # sample) is pinned to whatever it had at this instant for this Registry's
        # entire life.
        for p in providers:
            for mid in p.metrics():
                if not is_metric(mid):
                    raise ValueError(
                        f"provider {p.id!r} declares an unknown metric: {mid!r}")

        self._providers = sorted(providers, key=self._rank)
        self._available = []
        self._reasons: dict[str, str] = {}
        self._resolution: dict[str, str] = {}

        for p in self._providers:
            try:
                ok = p.probe()
            except Exception as e:                      # a broken probe must not stop start-up
                ok, p.unavailable_reason = False, f"detection failed: {e}"
            if ok:
                self._available.append(p)
            else:
                reason = p.unavailable_reason or _NO_PROVIDER
                for mid in p.metrics():
                    self._reasons.setdefault(mid, reason)

        # Per metric, EVERY available provider that serves it, in priority order.
        # self._available is already sorted, so each list comes out sorted. This is
        # what makes read()'s failover possible: before, only the winner was kept
        # and the substitutes were invisible.
        self._servers: dict[str, list[str]] = {}
        for p in self._available:
            for mid in p.metrics():
                self._servers.setdefault(mid, []).append(p.id)
                self._resolution.setdefault(mid, p.id)
                self._reasons.pop(mid, None)

        self._degraded: dict[str, str] = {}

    @staticmethod
    def _rank(p):
        try:
            return PROVIDER_PRIORITY.index(p.id)
        except ValueError:
            return len(PROVIDER_PRIORITY)

    def resolution(self) -> dict[str, str]:
        """metric id -> the provider id serving it right now."""
        return {m: pid for m, pid in self._resolution.items()
                if m not in self._degraded}

    def catalog(self) -> dict:
        """id -> MetricSpec with the best label available, for the editor.

        Only from AVAILABLE providers: offering the user a metric nobody serves is
        inviting them to place a widget that will show "--".

        The provider's label wins over metrics.spec_for()'s generic one because it
        is the only one that can name the real device: `vol.D.free` does not know
        that D is called "GAMES".
        """
        cat = {}
        for mid in self._servers:
            base = spec_for(mid)
            if base is not None:
                cat[mid] = base
        for p in self._available:
            try:
                cat.update(p.catalog())
            except Exception:
                pass                    # a broken catalogue must not bring the editor down
        return cat

    def groups(self) -> dict:
        """id -> device, for grouping the editor's list.

        Whatever the provider does not classify falls back to the prefix group from
        metrics.group_for(), which already returns a friendly name ("net" ->
        "Network") rather than the raw prefix.
        """
        g = {}
        for mid in self._servers:
            g[mid] = group_for(mid)
        for p in self._available:
            try:
                g.update(p.groups())
            except Exception:
                pass
        return g

    def unavailable(self) -> dict[str, str]:
        """metric id -> the reason, in plain language, for the editor to show.

        Only things with a concrete reason: a provider that did not start (with ITS
        own reason, "WinRing0 is on the blocklist", which says what to do about it)
        and whatever degraded in the last sample.

        **It deliberately does NOT list every metric nobody serves.** That was tried
        and it was noise: on a machine with no GPU it is 27 lines of "no data" even
        when the profile uses not one GPU metric, and a problem list that always has
        27 entries is a list the user stops reading. What matters is what the ACTIVE
        layout uses and cannot be served, and that is reported by
        Engine._sin_datos(), the only thing with the layout in front of it -- besides
        being the only possible route for family metrics (fan.N.rpm), which are a
        pattern and cannot be
        pueden enumerar.
        """
        return {**self._reasons, **self._degraded}

    def read(self):
        """One sample per pass, resolving each metric to the highest-priority
        provider that ACTUALLY answered this time.

        An earlier version pinned the owner at start-up and, when it failed, merely
        marked the metric degraded: the substitutes were skipped by
        `self._resolution.get(mid) != p.id`. With cpu.clock and cpu.name served by
        both pdh and psutil, a pdh failure sent both to "--" with psutil alive right
        beside it serving them perfectly well.

        The resolution is recalculated on every pass, so failover and falling back
        (when the original owner revives) come out of the same path, with no extra
        state to keep in sync.
        """
        samples, errors = {}, {}
        for p in self._available:
            try:
                samples[p.id] = p.read()
            except Exception as e:
                errors[p.id] = f"provider {p.id} fallo: {e}"

        out, degraded = {}, {}
        for mid, pids in self._servers.items():
            for pid in pids:
                if pid in samples:
                    self._resolution[mid] = pid
                    # .get(): the provider answered but may not carry this metric
                    # in this sample. None means "no data right now", which is not
                    # the same as UNAVAILABLE.
                    out[mid] = samples[pid].get(mid)
                    break
            else:
                degraded[mid] = next((errors[pid] for pid in pids if pid in errors),
                                     _NO_PROVIDER)
        self._degraded = degraded

        for mid in self.unavailable():
            out.setdefault(mid, UNAVAILABLE)
        return out

    def close(self):
        for p in self._providers:
            try:
                p.close()
            except Exception:
                pass
