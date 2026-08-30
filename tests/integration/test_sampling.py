"""Integration tests — phase 5 stats sampling engine (SPEC phase 5, W4).

These exercise the shared ``StatsSampler`` against the live container
daemon: a busy container must accumulate derived-rate history (with
CPU% > 0 under load) and the persistence file must round-trip through
``StatsHistory.from_json``.

The API endpoints behind the sampler (``GET /stats/history``, SSE
``GET /stats/stream``) were manually verified against the live server
in phase-5 W2 (curl: history window + stream yields), so no in-test
endpoint test lives here — the sampler/history/persistence contract is
what this file pins down.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import pytest

from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import PolicyViolationError
from daedalus.core.forge import Forge
from daedalus.core.sampling import StatsHistory, StatsSampler, StatsSampleRate
from daedalus.core.store import Store

TEST_IMAGE = "alpine:latest"

# A busy loop: burn ~half a second of CPU (500k increments), then idle
# one second, forever.  The VM's CPU clock must advance between ticks,
# so at least one derived rate should report cpu_percent > 0.
BUSY_LOOP = (
    "while true; do i=0; "
    "while [ $i -lt 500000 ]; do i=$((i+1)); done; "
    "sleep 1; done"
)


def _skip_on_policy(exc: BaseException) -> None:
    if isinstance(exc, PolicyViolationError) or "POLICY" in str(exc):
        pytest.skip("disk policy blocked container create on this host")


async def _run_detached_alpine(forge: Forge, command: list[str] | None = None) -> str:
    """Run a detached ``alpine:latest`` container; return its id.

    Skips when host policy blocks container creation.
    """
    try:
        lab = await forge.run(TEST_IMAGE, detach=True, command=command or ["sleep", "300"])
    except Exception as e:
        _skip_on_policy(e)
        raise
    return lab.id


async def _await_rates(
    sampler: StatsSampler,
    container_id: str,
    min_rates: int,
    timeout: float = 45.0,
) -> list[StatsSampleRate]:
    """Poll ``sampler.series`` until it has ``min_rates`` rates.

    One ``container stats`` pass against a running VM takes ~2.5 s on
    this runtime, so a fixed sleep window cannot guarantee a tick
    count — polling until the deadline is the deterministic option.
    """
    deadline = time.monotonic() + timeout
    rates: list[StatsSampleRate] = []
    while time.monotonic() < deadline:
        rates = sampler.series(container_id)
        if len(rates) >= min_rates:
            return rates
        await asyncio.sleep(1.0)
    return rates


@pytest.mark.integration
class TestStatsSamplingEngine:
    """StatsSampler against the live daemon: history + persistence."""

    async def test_sampler_accumulates_history_under_load(
        self,
        forge: Forge,
        backend: CliBackend,
        tmp_path: Path,
    ) -> None:
        """A CPU-burning container accumulates derived rates in the ring.

        The sampler's background loop runs at a 1 s cadence; the first
        tick of an id is the baseline and yields no rate, so ≥ 3 rates
        mean ≥ 4 observed ticks.  The busy loop must show up as
        cpu_percent > 0 in at least one rate.
        """
        cid = await _run_detached_alpine(forge, command=["sh", "-c", BUSY_LOOP])
        sampler = StatsSampler(
            backend,
            interval=1.0,
            persistence_path=str(tmp_path / "stats.json"),
        )
        try:
            await asyncio.sleep(3)  # let the VM boot before sampling starts
            sampler.start()
            assert sampler.is_running
            rates = await _await_rates(sampler, cid, 3)

            assert len(rates) >= 3, f"expected >=3 rates, got {len(rates)}"
            assert any(r.cpu_percent > 0.0 for r in rates), (
                "busy-loop container never reported cpu_percent > 0"
            )
            # The rate carried the gauge through: memory is non-zero in
            # every derived sample, and monotonic stamps are real.
            assert all(r.memory_bytes > 0 for r in rates)
        finally:
            sampler.stop()
            assert not sampler.is_running
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)

    async def test_history_persistence_roundtrip(
        self,
        forge: Forge,
        backend: CliBackend,
        tmp_path: Path,
    ) -> None:
        """History written to the persistence file survives a round-trip.

        The sampler saves every SAVE_EVERY=10 ticks, so instead of
        waiting ~10 s on the wall clock we drive ``_tick()`` directly:
        12 deterministic ticks (each a real backend stats pass) cross
        the save cadence.  A fresh ``StatsHistory.from_json`` over the
        file must reconstruct the same container ids and series.
        """
        cid = await _run_detached_alpine(forge, command=["sleep", "300"])
        path = tmp_path / "stats.json"
        sampler = StatsSampler(backend, interval=1.0, persistence_path=str(path))
        try:
            await asyncio.sleep(3)  # let the VM boot so ticks see it
            for _ in range(12):  # > SAVE_EVERY=10 ticks → one save
                await sampler._tick()  # noqa: SLF001 — deterministic drive
            sampler.stop()  # no background task; idempotent no-op
            assert path.exists(), "persistence file never written"

            history = StatsHistory.from_json(path.read_text())
            assert cid in history.ids(), f"{cid} missing from {history.ids()}"
            rates = history.series(cid)
            assert rates, "reloaded history has an empty series"
            # Derived-rate fields survived the JSON round-trip.
            assert all(r.at > 0.0 for r in rates)
            assert any(r.memory_bytes > 0 for r in rates)
        finally:
            sampler.stop()
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)
