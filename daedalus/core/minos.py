"""M7 — minos: the judge.

Minos consumes ariadne telemetry + ``stats`` fingerprints + before/after
``export`` tar diffs + boot logs, and produces:

1. A structured **behavioral report** (filesystem deltas, network attempts,
   syscall summary, resource profile)
2. An optional **risk score** (0.0–1.0)

Named for King Minos, who judged the Labyrinth.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from daedalus.core.backend import Backend

# ==========================================================================
# Evidence
# ==========================================================================


@dataclass
class FileSystemDelta:
    """Files added, modified, or removed during a run."""
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    total_changes: int = 0


@dataclass
class NetworkActivity:
    """Network connections observed during a run."""
    connections: list[dict[str, Any]] = field(default_factory=list)
    dns_queries: list[str] = field(default_factory=list)
    listening_ports: list[int] = field(default_factory=list)
    total_bytes_sent: int = 0
    total_bytes_received: int = 0


@dataclass
class SyscallSummary:
    """Aggregated syscall activity."""
    total_calls: int = 0
    unique_syscalls: int = 0
    top_calls: list[dict[str, Any]] = field(default_factory=list)
    suspicious_calls: list[str] = field(default_factory=list)


@dataclass
class ResourceProfile:
    """Resource usage during the run."""
    peak_cpu_percent: float = 0.0
    peak_memory_bytes: int = 0
    total_disk_writes: int = 0
    total_disk_reads: int = 0
    peak_pid_count: int = 0
    duration_seconds: float = 0.0


# ==========================================================================
# Report
# ==========================================================================


@dataclass
class BehavioralReport:
    """Complete behavioural analysis of a Labyrinth run."""

    run_id: str
    image: str = ""
    image_digest: str = ""

    # Evidence
    filesystem: FileSystemDelta = field(default_factory=FileSystemDelta)
    network: NetworkActivity = field(default_factory=NetworkActivity)
    syscalls: SyscallSummary = field(default_factory=SyscallSummary)
    resources: ResourceProfile = field(default_factory=ResourceProfile)

    # Scoring
    risk_score: float = 0.0
    risk_factors: list[str] = field(default_factory=list)

    # Metadata
    generated_at: str = ""
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "image": self.image,
            "image_digest": self.image_digest,
            "filesystem": {
                "added_count": len(self.filesystem.added),
                "modified_count": len(self.filesystem.modified),
                "removed_count": len(self.filesystem.removed),
                "total_changes": self.filesystem.total_changes,
                "added": self.filesystem.added[:50],
                "modified": self.filesystem.modified[:50],
                "removed": self.filesystem.removed[:50],
            },
            "network": {
                "connections": self.network.connections[:50],
                "dns_queries": self.network.dns_queries,
                "listening_ports": self.network.listening_ports,
                "total_bytes_sent": self.network.total_bytes_sent,
                "total_bytes_received": self.network.total_bytes_received,
            },
            "syscalls": {
                "total_calls": self.syscalls.total_calls,
                "unique_syscalls": self.syscalls.unique_syscalls,
                "top_calls": self.syscalls.top_calls[:20],
                "suspicious_calls": self.syscalls.suspicious_calls,
            },
            "resources": {
                "peak_cpu_percent": self.resources.peak_cpu_percent,
                "peak_memory_mb": self.resources.peak_memory_bytes / 1024 / 1024,
                "total_disk_writes": self.resources.total_disk_writes,
                "total_disk_reads": self.resources.total_disk_reads,
                "peak_pid_count": self.resources.peak_pid_count,
                "duration_seconds": self.resources.duration_seconds,
            },
            "risk_score": self.risk_score,
            "risk_factors": self.risk_factors,
            "findings": self.findings,
            "generated_at": self.generated_at,
        }

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Behavioral Report — {self.run_id[:12]}",
            "──────────────────────────────",
            f"Image:     {self.image}",
            f"Risk:      {self.risk_score:.2f}  ({_risk_label(self.risk_score)})",
            f"Duration:  {self.resources.duration_seconds:.1f}s",
            "",
            f"Filesystem: {self.filesystem.total_changes} changes "
            f"({len(self.filesystem.added)} added, "
            f"{len(self.filesystem.modified)} modified, "
            f"{len(self.filesystem.removed)} removed)",
            f"Network:    {len(self.network.connections)} connections, "
            f"{len(self.network.dns_queries)} DNS queries, "
            f"{self.network.total_bytes_sent + self.network.total_bytes_received} bytes",
            f"Syscalls:   {self.syscalls.total_calls} total, "
            f"{self.syscalls.unique_syscalls} unique",
            f"CPU peak:   {self.resources.peak_cpu_percent:.1f}%",
            f"Memory pk:  {self.resources.peak_memory_bytes / 1024 / 1024:.0f} MB",
        ]
        if self.risk_factors:
            lines.append("")
            lines.append("Risk factors:")
            for factor in self.risk_factors:
                lines.append(f"  ⚠ {factor}")
        if self.findings:
            lines.append("")
            lines.append("Findings:")
            for finding in self.findings:
                lines.append(f"  • {finding}")
        return "\n".join(lines)


# ==========================================================================
# Minos — the judge
# ==========================================================================


class Minos:
    """Forensic analysis engine.

    Consumes raw evidence and produces structured behavioural reports
    with risk scoring.
    """

    # ------------------------------------------------------------------
    # Suspicious indicators (presence increases risk score)
    # ------------------------------------------------------------------

    SUSPICIOUS_FS_PATHS: list[str] = [
        "/etc/passwd", "/etc/shadow", "/etc/hosts",
        "/root/.ssh", "/root/.ssh/*",
        "/home/*/.ssh", "/home/*/.ssh/*",
        "/tmp/*.sh", "/tmp/*.py", "/tmp/*.elf",
        "/var/spool/cron", "/etc/crontab",
    ]

    SUSPICIOUS_SYSCALLS: set[str] = {
        "ptrace", "process_vm_writev", "memfd_create",
        "unshare", "clone", "mount", "pivot_root",
        "kexec_load", "init_module", "finit_module",
        "bpf", "iopl", "ioperm",
    }

    def __init__(
        self,
        backend: Backend | None = None,
        ariadne: Any = None,
    ) -> None:
        self._backend = backend
        self._ariadne = ariadne

    # ==================================================================
    # Report generation
    # ==================================================================

    async def analyze(
        self,
        run_id: str,
        *,
        image: str = "",
        image_digest: str = "",
        pre_tar: str | None = None,
        post_tar: str | None = None,
        stats_data: list[dict[str, Any]] | None = None,
        telemetry_data: dict[str, Any] | None = None,
        boot_log: str | None = None,
    ) -> BehavioralReport:
        """Generate a full behavioral report from available evidence.

        Parameters
        ----------
        run_id:
            The container/run ID.
        pre_tar:
            Path to tar of the filesystem *before* the run.
        post_tar:
            Path to tar of the filesystem *after* the run.
        stats_data:
            List of ``container stats`` snapshots across the run.
        telemetry_data:
            Ariadne telemetry (syscalls, network, file events).
        boot_log:
            Boot-time log output.
        """
        report = BehavioralReport(
            run_id=run_id,
            image=image,
            image_digest=image_digest,
            generated_at=datetime.now(UTC).isoformat(),
        )

        # 1. Filesystem diff
        if pre_tar and post_tar:
            report.filesystem = self._diff_tars(pre_tar, post_tar)

        # 2. Stats analysis
        if stats_data:
            report.resources = self._analyze_stats(stats_data)

        # 3. Telemetry analysis
        if telemetry_data:
            report.syscalls = self._summarize_syscalls(telemetry_data)
            report.network = self._summarize_network(telemetry_data)

        # 4. Compute risk score
        report.risk_score, report.risk_factors = self._score(report)

        # 5. Generate findings
        report.findings = self._generate_findings(report)

        return report

    # ==================================================================
    # Internal analysis methods
    # ==================================================================

    def _diff_tars(self, pre: str, post: str) -> FileSystemDelta:
        """Diff two tarballs and return filesystem changes."""
        pre_files = self._tar_manifest(pre)
        post_files = self._tar_manifest(post)

        pre_set = set(pre_files.keys())
        post_set = set(post_files.keys())

        added = sorted(post_set - pre_set)
        removed = sorted(pre_set - post_set)
        modified = sorted(
            p for p in (pre_set & post_set)
            if pre_files[p] != post_files[p]
        )

        return FileSystemDelta(
            added=added,
            modified=modified,
            removed=removed,
            total_changes=len(added) + len(modified) + len(removed),
        )

    @staticmethod
    def _tar_manifest(tar_path: str) -> dict[str, str]:
        """Return {path: sha256} for every regular file in the tar."""
        manifest: dict[str, str] = {}
        if not os.path.exists(tar_path):
            return manifest
        try:
            with tarfile.open(tar_path) as tf:
                for member in tf.getmembers():
                    if member.isfile():
                        f = tf.extractfile(member)
                        if f:
                            manifest[member.name] = hashlib.sha256(
                                f.read()
                            ).hexdigest()
        except Exception:
            pass
        return manifest

    def _analyze_stats(
        self, snapshots: list[dict[str, Any]]
    ) -> ResourceProfile:
        """Extract peak resource usage from stats snapshots."""
        peak_cpu = 0.0
        peak_mem = 0
        peak_pids = 0
        total_disk_w = 0
        total_disk_r = 0

        for s in snapshots:
            peak_cpu = max(peak_cpu, float(s.get("cpu_percent", 0)))
            peak_mem = max(peak_mem, int(s.get("memory_usage", 0)))
            peak_pids = max(peak_pids, int(s.get("pid_count", 0)))
            total_disk_w = max(total_disk_w, int(s.get("block_write", 0)))
            total_disk_r = max(total_disk_r, int(s.get("block_read", 0)))

        # Duration from first to last snapshot
        duration = 0.0
        if len(snapshots) >= 2:
            try:
                t0 = snapshots[0].get("timestamp", "")
                t1 = snapshots[-1].get("timestamp", "")
                if t0 and t1:
                    from datetime import datetime
                    d0 = datetime.fromisoformat(t0)
                    d1 = datetime.fromisoformat(t1)
                    duration = (d1 - d0).total_seconds()
            except Exception:
                pass

        return ResourceProfile(
            peak_cpu_percent=peak_cpu,
            peak_memory_bytes=peak_mem,
            total_disk_writes=total_disk_w,
            total_disk_reads=total_disk_r,
            peak_pid_count=peak_pids,
            duration_seconds=duration,
        )

    def _summarize_syscalls(
        self, telemetry: dict[str, Any]
    ) -> SyscallSummary:
        """Summarise syscall telemetry."""
        calls: list[dict[str, Any]] = telemetry.get("syscalls", [])
        if not calls:
            return SyscallSummary()

        # Count by name
        counts: dict[str, int] = {}
        for c in calls:
            name = c.get("name", "unknown")
            counts[name] = counts.get(name, 0) + 1

        sorted_calls = sorted(counts.items(), key=lambda x: -x[1])
        unique = set(counts.keys())
        suspicious = sorted(unique & self.SUSPICIOUS_SYSCALLS)

        return SyscallSummary(
            total_calls=len(calls),
            unique_syscalls=len(unique),
            top_calls=[{"name": n, "count": c} for n, c in sorted_calls[:20]],
            suspicious_calls=suspicious,
        )

    def _summarize_network(
        self, telemetry: dict[str, Any]
    ) -> NetworkActivity:
        """Summarise network telemetry."""
        connections = telemetry.get("connections", [])
        dns = telemetry.get("dns_queries", [])
        listeners = telemetry.get("listening_ports", [])

        return NetworkActivity(
            connections=connections,
            dns_queries=dns,
            listening_ports=listeners,
            total_bytes_sent=sum(
                int(c.get("bytes_sent", 0)) for c in connections
            ),
            total_bytes_received=sum(
                int(c.get("bytes_received", 0)) for c in connections
            ),
        )

    # ==================================================================
    # Scoring
    # ==================================================================

    def _score(
        self, report: BehavioralReport
    ) -> tuple[float, list[str]]:
        """Compute risk score (0.0–1.0) and contributing factors."""
        score = 0.0
        factors: list[str] = []

        # Filesystem changes
        if report.filesystem.total_changes > 100:
            score += 0.2
            factors.append(f"Large filesystem changes ({report.filesystem.total_changes} files)")

        # Writes to sensitive paths
        sensitive_writes = [
            p for p in report.filesystem.added + report.filesystem.modified
            if any(fnmatch.fnmatch(p, s) for s in self.SUSPICIOUS_FS_PATHS)
        ]
        if sensitive_writes:
            score += 0.3
            factors.append(f"Writes to sensitive paths: {', '.join(sensitive_writes[:5])}")

        # Network activity
        if report.network.connections:
            score += 0.1
            factors.append(f"Network connections to {len(report.network.connections)} hosts")
        if report.network.dns_queries:
            score += 0.05
            factors.append("DNS queries observed")

        # Suspicious syscalls
        if report.syscalls.suspicious_calls:
            score += 0.2
            factors.append(
                f"Suspicious syscalls: {', '.join(report.syscalls.suspicious_calls[:5])}"
            )

        # High resource usage
        if report.resources.peak_cpu_percent > 80:
            score += 0.05
            factors.append(f"High CPU usage ({report.resources.peak_cpu_percent:.0f}%)")
        if report.resources.peak_memory_bytes > 512 * 1024 * 1024:
            score += 0.05
            factors.append(f"High memory usage ({report.resources.peak_memory_bytes / 1024 / 1024:.0f} MB)")

        return min(score, 1.0), factors

    def _generate_findings(self, report: BehavioralReport) -> list[str]:
        """Generate human-readable findings from the report."""
        findings: list[str] = []

        if report.filesystem.total_changes == 0:
            findings.append("No filesystem changes detected — sample may be inactive or sandboxed.")
        if report.filesystem.total_changes > 0:
            findings.append(
                f"{report.filesystem.total_changes} filesystem changes "
                f"({len(report.filesystem.added)} files added, "
                f"{len(report.filesystem.modified)} modified, "
                f"{len(report.filesystem.removed)} removed)."
            )
        if report.syscalls.suspicious_calls:
            findings.append(
                f"{len(report.syscalls.suspicious_calls)} suspicious syscalls observed."
            )
        if report.network.connections:
            findings.append(
                f"{len(report.network.connections)} network connections made."
            )
        if report.risk_score > 0.5:
            findings.append(
                f"ELEVATED risk score ({report.risk_score:.2f}) — recommend further analysis."
            )
        elif report.risk_score > 0.0:
            findings.append(
                f"Low risk score ({report.risk_score:.2f}) — likely benign."
            )
        else:
            findings.append("No risk indicators — sample appears benign.")

        return findings


def _risk_label(score: float) -> str:
    if score > 0.7:
        return "HIGH"
    if score > 0.3:
        return "MEDIUM"
    if score > 0.0:
        return "LOW"
    return "NONE"
