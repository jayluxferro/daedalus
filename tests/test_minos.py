"""Tests for M7 — minos forensic analysis."""

import fnmatch

from daedalus.core.minos import (
    BehavioralReport,
    FileSystemDelta,
    Minos,
    NetworkActivity,
    SyscallSummary,
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
