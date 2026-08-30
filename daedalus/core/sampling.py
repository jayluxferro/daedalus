"""M5 — sampling: the stats observation engine (SPEC phase 5, W1).

Ported from Orchard's ``StatsService`` pattern, adapted to daedalus's
L1 + Python stack.  Where Orchard samples via an L2 daemon, we shell
``container stats --no-stream --format json`` (one-shot) on a cadence.

* ``StatsSampleRate`` — a derived two-tick sample (deltas + gauges),
  computed from two raw ``StatsSample`` records.
* ``StatsHistory`` — a bounded ring per container id (300 points ~= 10
  minutes at the default 2 s cadence), serialisable to a single JSON
  file for persistence across restarts.  ``host`` is "local" today; the
  keying by container id alone supports multi-host later (same note as
  orchard's).
* ``StatsSampler`` — owns the cadence, the previous-raw-sample table,
  the asyncio background loop, and the persistence cadence
  (save every ``SAVE_EVERY`` ticks, load on boot).
* ``get_sampler(backend)`` — the per-process singleton (one global; a
  ``Forge``/MCP/CLI caller and the API server all share it).

Detonation evidence note: ``Forge.detonate`` keeps its inline raw
``StatsSample`` loop as the *evidence* source (minos's
``stats_samples_to_snapshots`` consumes cumulative raws with pairwise
``cpu_percent``, which derived rates cannot reproduce) and runs the
shared sampler *alongside* so the run's stats also land in the global
history ring — the data spine for the live UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from daedalus.core.backend import Backend, StatsSample

# Per-container history cap — ~10 minutes at the default 2 s cadence.
MAX_POINTS = 300

# Persistence cadence: write the history file every N successful ticks.
SAVE_EVERY = 10

# Mirrors ``Store``'s default root ("~/.daedalus/store/") in store.py —
# the stats history persists next to the run store, not in a new
# directory.
DEFAULT_PERSISTENCE_PATH = os.path.expanduser(
    "~/.daedalus/store/stats-history.json",
)

_log = logging.getLogger("daedalus.sampling")


@dataclass
class StatsSampleRate:
    """A derived two-tick rate sample for a container.

    Produced by :meth:`StatsSampler._derive_rate` from two raw
    ``StatsSample`` records: counters (network, block I/O) become
    deltas clamped at zero (counter resets between ticks are treated
    as no activity), memory and process counts are gauges carried
    through from the newer sample, and ``cpu_percent`` delegates to
    ``StatsSample.cpu_percent`` (pairwise, clamped to 0..100).
    ``at`` is the monotonic timestamp of the newer raw sample.
    """

    id: str
    cpu_percent: float = 0.0
    memory_bytes: int = 0
    memory_limit_bytes: int = 0
    net_rx_delta: int = 0
    net_tx_delta: int = 0
    block_r_delta: int = 0
    block_w_delta: int = 0
    num_processes: int = 0
    at: float = 0.0  # time.monotonic()

    def to_dict(self) -> dict[str, Any]:
        """Serialise the rate as a flat dict."""
        return {
            "id": self.id,
            "cpu_percent": self.cpu_percent,
            "memory_bytes": self.memory_bytes,
            "memory_limit_bytes": self.memory_limit_bytes,
            "net_rx_delta": self.net_rx_delta,
            "net_tx_delta": self.net_tx_delta,
            "block_r_delta": self.block_r_delta,
            "block_w_delta": self.block_w_delta,
            "num_processes": self.num_processes,
            "at": self.at,
        }


class StatsHistory:
    """Bounded per-container history of derived rate samples.

    Each container id maps to a ``deque(maxlen=MAX_POINTS)`` ring —
    appending past the cap evicts the oldest point.  Rows are kept
    oldest→newest and are filtered by a monotonic ``at`` window on
    read.  The whole history (rates only, never the raw samples they
    were derived from) serialises to one JSON document.
    """

    def __init__(self) -> None:
        self._series: dict[str, deque[StatsSampleRate]] = {}

    def append(self, rate: StatsSampleRate) -> None:
        """Append a rate to the container's ring, evicting the oldest."""
        self._series.setdefault(rate.id, deque(maxlen=MAX_POINTS)).append(rate)

    def series(
        self,
        container_id: str,
        seconds: float | None = None,
        *,
        now: float | None = None,
    ) -> list[StatsSampleRate]:
        """Return a container's rates, oldest→newest.

        With ``seconds``, only rates with ``at >= now - seconds`` are
        returned (the trailing window).  ``now`` defaults to
        :func:`time.monotonic` and is injectable for testability.
        """
        rates = self._series.get(container_id)
        if rates is None:
            return []
        if seconds is None:
            return list(rates)
        cutoff = (now if now is not None else time.monotonic()) - seconds
        return [rate for rate in rates if rate.at >= cutoff]

    def ids(self) -> list[str]:
        """Container ids that have any history."""
        return list(self._series.keys())

    def to_json(self) -> str:
        """Serialize all series (rates only) as one JSON document."""
        return json.dumps(
            {
                "series": {
                    cid: [rate.to_dict() for rate in rates]
                    for cid, rates in self._series.items()
                },
            },
        )

    @classmethod
    def from_json(cls, text: str) -> StatsHistory:
        """Reconstruct a history from :meth:`to_json` output.

        Corrupted or partial JSON logs the failure and yields an empty
        history — the sampler must start clean rather than crash the
        process (fail loudly in logs, not crash).
        """
        try:
            data: Any = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("stats history JSON root must be an object")
            series: Any = data.get("series", {})
            if not isinstance(series, dict):
                raise ValueError("stats history 'series' must be an object")
            history = cls()
            for cid, raw_rates in series.items():
                if not isinstance(raw_rates, list):
                    raise ValueError(f"series for '{cid}' must be a list")
                for raw in raw_rates:
                    if not isinstance(raw, dict):
                        raise ValueError(f"series entry for '{cid}' must be an object")
                    history.append(StatsSampleRate(**raw))
            return history
        except Exception as exc:
            _log.error("stats history load failed — starting empty: %s", exc)
            return cls()


class StatsSampler:
    """Periodic two-tick stats sampling with history and persistence.

    Parameters
    ----------
    backend:
        The backend to sample (``stats(None)`` → all running).
    interval:
        Sampling cadence in seconds; floored at 0.5 s so a zero or
        negative value cannot busy-loop the backend.
    history:
        Optional pre-built history; a fresh one is created otherwise.
    persistence_path:
        Where the history JSON is written every ``SAVE_EVERY`` ticks
        and loaded from on construction.  Defaults to
        ``~/.daedalus/store/stats-history.json`` (the run-store root,
        mirroring ``Store``'s).
    """

    def __init__(
        self,
        backend: Backend,
        interval: float = 2.0,
        history: StatsHistory | None = None,
        persistence_path: str | None = None,
    ) -> None:
        self._backend = backend
        self._interval = max(interval, 0.5)
        self._history = history or StatsHistory()
        self._persistence_path = persistence_path or DEFAULT_PERSISTENCE_PATH
        # Previous raw sample per container id — the first observation
        # of an id yields no rate (there is no baseline to diff).
        self._prev: dict[str, StatsSample] = {}
        self._task: asyncio.Task[None] | None = None
        self._ticks = 0
        # Distinct error messages already logged — repeated failures of
        # the same cause are logged once, not on every tick.
        self._seen_errors: set[str] = set()
        self._load()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start the background sampling loop (idempotent).

        Creates the asyncio task if none is running; a second call
        while running is a no-op.
        """
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        """Stop the background sampling loop (idempotent).

        Cancels the task if one is running.  With the per-process
        singleton this is best-effort — a caller that does not own the
        sampler (e.g. the API server in wave 2) may be stopped by an
        earlier caller's stop(); ownership coordination is the wave-2
        integration's job.
        """
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()

    @property
    def is_running(self) -> bool:
        """True while the background sampling loop is active.

        The API's detonation reconciliation checks this before
        restarting the shared sampler that ``Forge.detonate`` stopped.
        """
        return self._task is not None and not self._task.done()

    # -- sampling --------------------------------------------------------------

    async def _tick(self) -> None:
        """One sampling pass: derive rates for every running container.

        Calls ``stats(None)`` (all running), computes a rate per id
        against the previous raw sample (first observation of an id
        yields no rate), and appends the rates to history.  When the
        stats call raises, the tick is skipped — the error is logged
        once per distinct message and the loop does not crash
        (orchard-style resilience).  Empty passes (no containers
        running) are cheap and do not consume the save cadence.
        """
        raw = await self._sample_all()
        if raw is None or not raw:
            return
        self._ticks += 1
        for sample in raw:
            rate = self._derive_rate(sample, self._prev.get(sample.id))
            self._prev[sample.id] = sample
            if rate is not None:
                self._history.append(rate)
        if self._ticks % SAVE_EVERY == 0:
            self._save()

    async def _sample_all(self) -> list[StatsSample] | None:
        """One stats pass over all running containers; None on failure.

        The error handling lives here so a failing tick degrades to a
        skipped pass: the error is logged once per distinct message
        and None returns, keeping the loop alive (orchard-style
        resilience).
        """
        try:
            return await self._backend.stats(None)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if message not in self._seen_errors:
                self._seen_errors.add(message)
                _log.error("stats sampling tick failed — skipping: %s", exc)
            return None

    async def _run(self) -> None:
        """Background loop: sleep the interval, then one sampling pass."""
        while True:
            await asyncio.sleep(self._interval)
            await self._tick()

    async def snapshot(self, container_id: str | None = None) -> list[StatsSample]:
        """One-shot passthrough to ``backend.stats`` for on-demand callers."""
        return await self._backend.stats(container_id)

    def series(
        self,
        container_id: str,
        seconds: float | None = None,
        *,
        now: float | None = None,
    ) -> list[StatsSampleRate]:
        """Trailing history window for a container (delegates to history).

        ``now`` is injectable for testability, mirroring
        :meth:`StatsHistory.series`.
        """
        return self._history.series(container_id, seconds, now=now)

    # -- rate derivation ------------------------------------------------------

    @staticmethod
    def _derive_rate(
        cur: StatsSample,
        prev: StatsSample | None,
    ) -> StatsSampleRate | None:
        """Derive a rate sample from the previous raw sample.

        Returns None when there is no previous sample (first
        observation of the container).  Counters become deltas clamped
        at zero (a counter reset between ticks reads as no activity);
        ``cpu_percent`` delegates to ``StatsSample.cpu_percent`` which
        guards zero/negative deltas and clamps to 0..100; memory and
        process counts are gauges taken from ``cur``.
        """
        if prev is None:
            return None
        return StatsSampleRate(
            id=cur.id,
            cpu_percent=cur.cpu_percent(prev),
            memory_bytes=cur.memory_usage_bytes,
            memory_limit_bytes=cur.memory_limit_bytes,
            net_rx_delta=max(0, cur.network_rx_bytes - prev.network_rx_bytes),
            net_tx_delta=max(0, cur.network_tx_bytes - prev.network_tx_bytes),
            block_r_delta=max(0, cur.block_read_bytes - prev.block_read_bytes),
            block_w_delta=max(0, cur.block_write_bytes - prev.block_write_bytes),
            num_processes=cur.num_processes,
            at=cur.sampled_at,
        )

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        """Load persisted history when the file exists (failure-safe)."""
        path = self._persistence_path
        if path is None or not os.path.exists(path):
            return
        try:
            with open(path) as f:
                text = f.read()
        except OSError as exc:
            _log.error("stats history load failed (%s): %s", path, exc)
            return
        self._history = StatsHistory.from_json(text)

    def _save(self) -> None:
        """Write the history to the persistence path (failure-safe)."""
        path = self._persistence_path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(self._history.to_json())
        except OSError as exc:
            _log.error("stats history save failed (%s): %s", path, exc)


# Per-process singleton: one sampler per backend process, shared by
# forge (detonation), the API server (wave 2), and MCP/CLI callers.
_sampler: StatsSampler | None = None


def get_sampler(backend: Backend) -> StatsSampler:
    """Return the process-wide ``StatsSampler`` singleton.

    One instance per process (keyed implicitly — the first backend
    wins; subsequent callers share the same sampler).  ``start()`` /
    ``stop()`` are idempotent so concurrent callers can start/stop
    without clobbering each other, though ownership of ``stop()`` is
    deliberately loose (see :meth:`StatsSampler.stop`).
    """
    global _sampler
    if _sampler is None:
        _sampler = StatsSampler(backend)
    return _sampler


# Re-export for convenience (module name ≠ class name).
__all__ = [
    "DEFAULT_PERSISTENCE_PATH",
    "MAX_POINTS",
    "SAVE_EVERY",
    "StatsHistory",
    "StatsSampleRate",
    "StatsSampler",
    "get_sampler",
]
