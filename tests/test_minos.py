"""Tests for M7 — minos forensic analysis."""

import pytest
import fnmatch

from daedalus.core.backend import StatsSample
from daedalus.core.minos import (
    BehavioralReport,
    FileSystemDelta,
    Minos,
    NetworkActivity,
    SyscallSummary,
    stats_samples_to_snapshots,
)


class TestSuspiciousPaths:
    """Verify fnmatch fix for SUSPICIOUS_FS_PATHS matching."""

    def test_exact_match(self):
        assert fnmatch.fnmatch("/etc/passwd", "/etc/passwd")
        assert fnmatch.fnmatch("/etc/shadow", "/etc/shadow")

    def test_glob_match_ssh(self):
        """Paths inside /home/*/.ssh should match."""
        # Directory-level match: the .ssh directory itself
        assert fnmatch.fnmatch("/home/alice/.ssh", "/home/*/.ssh")
        # Files inside: need /* suffix
        assert fnmatch.fnmatch("/home/alice/.ssh/id_rsa", "/home/*/.ssh/*")
        assert fnmatch.fnmatch("/home/bob/.ssh/authorized_keys", "/home/*/.ssh/*")

    def test_glob_no_match(self):
        """Non-ssh files in /home should NOT match /home/*/.ssh."""
        assert not fnmatch.fnmatch("/home/alice/Downloads/doc.pdf", "/home/*/.ssh")
        assert not fnmatch.fnmatch("/home/bob/Documents/note.txt", "/home/*/.ssh")

    def test_tmp_elf_match(self):
        assert fnmatch.fnmatch("/tmp/evil.elf", "/tmp/*.elf")
        assert fnmatch.fnmatch("/tmp/trojan.sh", "/tmp/*.sh")
        assert fnmatch.fnmatch("/tmp/dropper.py", "/tmp/*.py")

    def test_tmp_no_match(self):
        assert not fnmatch.fnmatch("/tmp/thing.txt", "/tmp/*.elf")
        assert not fnmatch.fnmatch("/tmp/thing.txt", "/tmp/*.sh")
        assert not fnmatch.fnmatch("/tmp/thing.txt", "/tmp/*.py")


class TestRiskScoring:
    def test_empty_report_zero_score(self):
        m = Minos()
        report = BehavioralReport(run_id="test")
        score, factors = m._score(report)
        assert score == 0.0
        assert factors == []

    def test_large_fs_changes_increase_score(self):
        m = Minos()
        report = BehavioralReport(
            run_id="test",
            filesystem=FileSystemDelta(total_changes=200, added=["/tmp/a"] * 200),
        )
        score, factors = m._score(report)
        assert score >= 0.2  # at minimum the large changes factor
        assert any("Large filesystem" in f for f in factors)

    def test_sensitive_paths_increase_score(self):
        m = Minos()
        report = BehavioralReport(
            run_id="test",
            filesystem=FileSystemDelta(
                added=["/etc/passwd", "/tmp/evil.sh"],
                total_changes=2,
            ),
        )
        score, factors = m._score(report)
        assert score >= 0.3
        assert any("sensitive paths" in f.lower() for f in factors)

    def test_network_activity_increases_score(self):
        m = Minos()
        report = BehavioralReport(
            run_id="test",
            network=NetworkActivity(
                connections=[{"host": "evil.com"}],
                dns_queries=["bad.com"],
                total_bytes_sent=1024,
            ),
        )
        score, factors = m._score(report)
        assert score > 0.0

    def test_suspicious_syscalls_increase_score(self):
        m = Minos()
        report = BehavioralReport(
            run_id="test",
            syscalls=SyscallSummary(
                total_calls=100,
                unique_syscalls=5,
                suspicious_calls=["ptrace", "memfd_create", "bpf"],
            ),
        )
        score, factors = m._score(report)
        assert score >= 0.2
        assert any("syscalls" in f.lower() for f in factors)


class TestFindingsGeneration:
    def test_benign_sample(self):
        m = Minos()
        report = BehavioralReport(run_id="test")
        findings = m._generate_findings(report)
        assert any("benign" in f.lower() for f in findings)

    def test_malicious_sample(self):
        m = Minos()
        report = BehavioralReport(
            run_id="test",
            filesystem=FileSystemDelta(added=["/tmp/bad.elf"], total_changes=5),
        )
        report.risk_score = 0.8
        report.risk_factors = ["Suspicious syscalls"]
        findings = m._generate_findings(report)
        assert any("elevated" in f.lower() or "elevated" in f.lower() for f in findings)


class TestStatsSamplesToSnapshots:
    """Phase 3 (W1) — the StatsSample → minos snapshot adapter."""

    def test_stats_samples_to_snapshots_empty(self):
        assert stats_samples_to_snapshots([]) == []

    def test_stats_samples_to_snapshots_single(self):
        s = StatsSample(
            id="c1",
            cpu_usage_usec=1000,
            sampled_at=10.0,
            memory_usage_bytes=2048,
            num_processes=3,
            block_write_bytes=1024,
            block_read_bytes=512,
        )
        out = stats_samples_to_snapshots([s])
        assert len(out) == 1
        assert out[0]["cpu_percent"] == 0.0
        assert out[0]["monotonic"] == 10.0

    def test_stats_samples_to_snapshots_mapping(self):
        prev = StatsSample(
            id="c1",
            cpu_usage_usec=1000,
            sampled_at=10.0,
            memory_usage_bytes=1024,
            num_processes=2,
            block_write_bytes=4096,
            block_read_bytes=256,
        )
        cur = StatsSample(
            id="c1",
            cpu_usage_usec=2000,
            sampled_at=12.0,
            memory_usage_bytes=2048,
            num_processes=4,
            block_write_bytes=8192,
            block_read_bytes=512,
        )
        snapshots = stats_samples_to_snapshots([prev, cur])

        assert snapshots[0]["cpu_percent"] == 0.0
        # The adapter must not re-implement the rate math — it delegates to
        # StatsSample.cpu_percent (1000 usec over 2 s = 0.05% of one core).
        assert snapshots[1]["cpu_percent"] == cur.cpu_percent(prev)
        assert snapshots[1]["cpu_percent"] == pytest.approx(0.05)

        assert snapshots[1]["memory_usage"] == cur.memory_usage_bytes
        assert snapshots[1]["pid_count"] == cur.num_processes
        assert snapshots[1]["block_write"] == cur.block_write_bytes
        assert snapshots[1]["block_read"] == cur.block_read_bytes
        assert snapshots[1]["timestamp"] == str(cur.sampled_at)
        assert snapshots[1]["monotonic"] == cur.sampled_at

        # First sample sources its keys from ``prev``.
        assert snapshots[0]["memory_usage"] == prev.memory_usage_bytes
        assert snapshots[0]["pid_count"] == prev.num_processes
        assert snapshots[0]["monotonic"] == prev.sampled_at


class TestAnalyzeStatsDuration:
    """Phase 3 (W1) — duration via the monotonic branch / ISO fallback."""

    def test_analyze_stats_duration_from_monotonic(self):
        rp = Minos()._analyze_stats(
            [
                {"monotonic": 10.0, "cpu_percent": 0},
                {"monotonic": 22.0, "cpu_percent": 5},
            ]
        )
        assert rp.duration_seconds == 12.0
        assert rp.peak_cpu_percent == 5.0

    def test_analyze_stats_datetime_fallback(self):
        rp = Minos()._analyze_stats(
            [
                {"timestamp": "2026-01-01T00:00:00+00:00", "cpu_percent": 1},
                {"timestamp": "2026-01-01T00:00:05+00:00", "cpu_percent": 2},
            ]
        )
        assert rp.duration_seconds == 5.0
        assert rp.peak_cpu_percent == 2.0
