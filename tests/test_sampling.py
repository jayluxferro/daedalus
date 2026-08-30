"""Tests for phase 5 W1 — stats sampling engine (daedalus.core.sampling)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque

import pytest

from daedalus.core.backend import StatsSample
from daedalus.core.sampling import (
    MAX_POINTS,
    StatsHistory,
    StatsSampler,
    StatsSampleRate,
    get_sampler,
)
from tests.test_forge import MockBackend

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _sample(
    cid: str = "c1",
    *,
    cpu: int = 0,
    at: float = 0.0,
    mem: int = 0,
    limit: int = 0,
    rx: int = 0,
    tx: int = 0,
    br: int = 0,
    bw: int = 0,
    procs: int = 0,
) -> StatsSample:
    """A raw StatsSample with explicit cumulative counters + monotonic stamp."""
    return StatsSample(
        id=cid,
        cpu_usage_usec=cpu,
        memory_usage_bytes=mem,
        memory_limit_bytes=limit,
        network_rx_bytes=rx,
        network_tx_bytes=tx,
        block_read_bytes=br,
        block_write_bytes=bw,
        num_processes=procs,
        sampled_at=at,
    )


def _rate(cid: str, at: float, cpu: float = 0.0) -> StatsSampleRate:
    return StatsSampleRate(id=cid, cpu_percent=cpu, at=at)


class QueuedStatsBackend(MockBackend):
    """MockBackend whose ``stats()`` pops canned responses off a queue.

    Lets ticks observe *different* raw samples (the stock MockBackend
    returns the same ``stats_result`` every call, which cannot exercise
    two-tick rate derivation).
    """

    def __init__(self, responses: list[list[StatsSample]] | None = None) -> None:
        super().__init__()
        self.queue: deque[list[StatsSample]] = deque(responses or [])
        self.fail = False

    async def stats(self, container_id: str | None = None) -> list[StatsSample]:
        self.call_log.append("stats")
        if self.fail:
            raise RuntimeError("stats boom")
        if self.queue:
            return list(self.queue.popleft())
        return []


# ------------------------------------------------------------------
# Rate derivation (StatsSampler._derive_rate)
# ------------------------------------------------------------------


class TestDeriveRate:
    def test_no_prev_is_none(self) -> None:
        assert StatsSampler._derive_rate(_sample(), None) is None

    def test_deltas_and_gauges(self) -> None:
        prev = _sample(
            cpu=1_000_000, at=1000.0, mem=100, limit=1024,
            rx=1_000, tx=500, br=64, bw=32, procs=1,
        )
        cur = _sample(
            cpu=3_000_000, at=1002.0, mem=200, limit=2048,
            rx=1_500, tx=700, br=128, bw=100, procs=2,
        )
        rate = StatsSampler._derive_rate(cur, prev)
        assert rate is not None
        # cpu: (2_000_000 usec / 1e6) / 2 s * 100 = 100%
        assert rate.cpu_percent == 100.0
        # memory is a gauge (no delta)
        assert rate.memory_bytes == 200
        assert rate.memory_limit_bytes == 2048
        assert rate.net_rx_delta == 500
        assert rate.net_tx_delta == 200
        assert rate.block_r_delta == 64
        assert rate.block_w_delta == 68
        assert rate.num_processes == 2
        assert rate.at == 1002.0

    def test_cpu_percent_delegation_and_clamping(self) -> None:
        prev = _sample(cpu=0, at=1000.0)
        # Delegates to StatsSample.cpu_percent: 1 core-second over 2 s = 50%.
        mid = StatsSampler._derive_rate(_sample(cpu=1_000_000, at=1002.0), prev)
        assert mid is not None
        assert mid.cpu_percent == 50.0
        # No CPU advance → 0.0 (zero-delta guard).
        flat = StatsSampler._derive_rate(_sample(cpu=0, at=1002.0), prev)
        assert flat is not None
        assert flat.cpu_percent == 0.0
        # Counter jump → clamped to 100.0.
        huge = StatsSampler._derive_rate(_sample(cpu=50_000_000, at=1002.0), prev)
        assert huge is not None
        assert huge.cpu_percent == 100.0

    def test_counter_reset_deltas_guarded(self) -> None:
        prev = _sample(at=1000.0, rx=10_000, br=5_000)
        cur = _sample(at=1002.0, rx=2_000, br=500)
        rate = StatsSampler._derive_rate(cur, prev)
        assert rate is not None
        assert rate.net_rx_delta == 0
        assert rate.block_r_delta == 0
        assert rate.cpu_percent == 0.0


# ------------------------------------------------------------------
# StatsHistory
# ------------------------------------------------------------------


class TestHistory:
    def test_append_order(self) -> None:
        history = StatsHistory()
        for i in range(1, 4):
            history.append(_rate("c1", float(i)))
        assert [r.at for r in history.series("c1")] == [1.0, 2.0, 3.0]

    def test_cap_eviction(self) -> None:
        history = StatsHistory()
        for i in range(MAX_POINTS + 1):
            history.append(_rate("c1", float(i)))
        rates = history.series("c1")
        assert len(rates) == MAX_POINTS
        assert rates[0].at == 1.0  # oldest evicted
        assert rates[-1].at == float(MAX_POINTS)

    def test_series_window(self) -> None:
        history = StatsHistory()
        for i in range(1, 11):
            history.append(_rate("c1", float(i)))
        # cutoff = now - seconds = 10.5 - 5 = 5.5 → rates at 6..10
        assert [r.at for r in history.series("c1", seconds=5, now=10.5)] == [
            6.0, 7.0, 8.0, 9.0, 10.0,
        ]
        # No window → everything.
        assert len(history.series("c1")) == 10
        # Unknown id → empty.
        assert history.series("missing") == []

    def test_ids(self) -> None:
        history = StatsHistory()
        history.append(_rate("a", 1.0))
        history.append(_rate("b", 2.0))
        assert set(history.ids()) == {"a", "b"}
        assert StatsHistory().ids() == []

    def test_to_json_round_trip(self) -> None:
        history = StatsHistory()
        history.append(_rate("c1", 1.0, cpu=12.5))
        history.append(_rate("c1", 2.0))
        history.append(_rate("c2", 5.0))
        clone = StatsHistory.from_json(history.to_json())
        assert clone.to_json() == history.to_json()
        assert [r.to_dict() for r in clone.series("c1")] == [
            r.to_dict() for r in history.series("c1")
        ]

    def test_corrupted_json_is_empty(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.ERROR, logger="daedalus.sampling"):
            history = StatsHistory.from_json("{not json")
        assert history.ids() == []
        assert "stats history" in caplog.text
        # Structurally wrong → also empty (fail loudly in logs, not crash).
        with caplog.at_level(logging.ERROR, logger="daedalus.sampling"):
            wrong = StatsHistory.from_json('{"series": "nope"}')
        assert wrong.ids() == []


# ------------------------------------------------------------------
# StatsSampler
# ------------------------------------------------------------------


class TestSampler:
    async def test_first_tick_no_rate_second_tick_derives(self) -> None:
        backend = QueuedStatsBackend([
            [_sample(cpu=0, at=1000.0, mem=1024, limit=4096, procs=1)],
            [_sample(cpu=1_000_000, at=1002.0, mem=2048, limit=4096, procs=2)],
        ])
        sampler = StatsSampler(backend, persistence_path=None)
        await sampler._tick()
        assert sampler.series("c1") == []
        await sampler._tick()
        rates = sampler.series("c1")
        assert len(rates) == 1
        assert rates[0].cpu_percent == 50.0
        assert rates[0].memory_bytes == 2048
        assert rates[0].net_rx_delta == 0
        assert rates[0].num_processes == 2
        assert rates[0].at == 1002.0

    async def test_start_stop_lifecycle(self) -> None:
        sampler = StatsSampler(QueuedStatsBackend(), persistence_path=None)
        assert sampler._task is None
        sampler.start()
        task = sampler._task
        assert task is not None and not task.done()
        sampler.start()  # idempotent while running
        assert sampler._task is task
        sampler.stop()
        sampler.stop()  # idempotent when already stopped
        assert sampler._task is None
        await asyncio.gather(task, return_exceptions=True)
        assert task.done()

    def test_interval_floored_at_half_second(self) -> None:
        low = StatsSampler(QueuedStatsBackend(), interval=0.01, persistence_path=None)
        assert low._interval == 0.5
        normal = StatsSampler(QueuedStatsBackend(), interval=3.0, persistence_path=None)
        assert normal._interval == 3.0

    async def test_tick_error_logged_once_and_recovers(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        backend = QueuedStatsBackend()
        backend.fail = True
        sampler = StatsSampler(backend, persistence_path=None)
        with caplog.at_level(logging.ERROR, logger="daedalus.sampling"):
            await sampler._tick()
            await sampler._tick()
        failures = [r for r in caplog.records if "sampling tick failed" in r.getMessage()]
        assert len(failures) == 1  # logged once per distinct error message
        # Recovery: a later good tick works normally.
        backend.fail = False
        backend.queue.append([_sample(cpu=0, at=10.0)])
        await sampler._tick()  # first observation → baseline only
        backend.queue.append([_sample(cpu=1_000_000, at=12.0)])
        await sampler._tick()
        assert len(sampler.series("c1")) == 1

    async def test_empty_pass_is_cheap_and_ignored(self) -> None:
        backend = QueuedStatsBackend()
        sampler = StatsSampler(backend, persistence_path=None)
        await sampler._tick()  # no containers → nothing recorded
        assert sampler.series("c1") == []
        assert sampler._ticks == 0

    async def test_snapshot_passthrough(self) -> None:
        backend = MockBackend()
        sampler = StatsSampler(backend, persistence_path=None)
        assert await sampler.snapshot("mock-0001") == backend.stats_result
        assert backend.stats_calls == ["mock-0001"]
        assert await sampler.snapshot() == backend.stats_result
        assert backend.stats_calls == ["mock-0001", None]

    async def test_series_delegates_to_history(self) -> None:
        backend = QueuedStatsBackend([
            [_sample(cpu=0, at=100.0)],
            [_sample(cpu=1_000_000, at=102.0)],
            [_sample(cpu=2_000_000, at=104.0)],
        ])
        sampler = StatsSampler(backend, persistence_path=None)
        for _ in range(3):
            await sampler._tick()
        assert len(sampler.series("c1")) == 2
        assert [r.at for r in sampler.series("c1", seconds=1, now=104.5)] == [104.0]


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


class TestPersistence:
    async def test_saves_every_n_ticks_and_reloads(self, tmp_path) -> None:
        path = str(tmp_path / "stats-history.json")
        responses = [[_sample(cpu=0, at=1000.0 + i)] for i in range(12)]
        sampler = StatsSampler(QueuedStatsBackend(responses), persistence_path=path)
        for _ in range(10):
            await sampler._tick()
        assert os.path.exists(path)
        with open(path) as f:
            data = json.loads(f.read())
        assert "c1" in data["series"]
        assert len(data["series"]["c1"]) == 9  # first tick yields no rate

        reloaded = StatsSampler(QueuedStatsBackend(), persistence_path=path)
        rates = reloaded.series("c1")
        assert len(rates) == 9
        assert rates[0].at == 1001.0

    async def test_corrupted_file_loads_empty(self, tmp_path) -> None:
        path = tmp_path / "stats-history.json"
        path.write_text("{corrupt")
        sampler = StatsSampler(QueuedStatsBackend(), persistence_path=str(path))
        assert sampler.series("c1") == []


# ------------------------------------------------------------------
# get_sampler singleton
# ------------------------------------------------------------------


class TestGetSampler:
    def test_singleton_same_backend(self) -> None:
        backend = MockBackend()
        assert get_sampler(backend) is get_sampler(backend)
