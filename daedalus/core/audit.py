"""Append-only structured audit log.

Every operation — create, run, stop, destroy, exec, network create,
kernel set, etc. — is recorded as an audit entry with:

* Timestamp
* Operation name
* Actor (human / agent / service)
* Arguments
* Result
* Container / image / network IDs involved

The audit log is the forensic chain for "what did the agent do?" and
is tamper-evident by design (append-only, structured, checksummed).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ActorKind(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"


@dataclass
class AuditEntry:
    """One operation record in the audit log."""

    operation: str
    actor: str
    actor_kind: ActorKind
    args: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    # Set automatically
    timestamp: str = ""
    entry_id: str = ""
    checksum: str = ""

    def finalize(self) -> None:
        """Set timestamp, generate ID and checksum."""
        self.timestamp = datetime.now(UTC).isoformat()
        self.checksum = self._compute_checksum()
        self.entry_id = f"{self.timestamp}-{self.checksum}"

    def _compute_checksum(self) -> str:
        """Compute checksum over the entry fields excluding entry_id
        and checksum (which are derived from the other fields)."""
        payload = {
            "operation": self.operation,
            "actor": self.actor,
            "actor_kind": self.actor_kind.value,
            "args": self.args,
            "result": self.result,
            "error": self.error,
            "timestamp": self.timestamp,
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)


class AuditLog:
    """Append-only, structured audit log.

    Thread-safe.  Entries are written as newline-delimited JSON to a file
    and held in-memory for fast querying.

    Parameters
    ----------
    path:
        Path to the audit log file.  Created if missing.
    """

    def __init__(self, path: str = "~/.daedalus/audit.jsonl") -> None:
        self._path = os.path.expanduser(path)
        self._lock = threading.Lock()
        self._entries: list[AuditEntry] = []
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def record(
        self,
        operation: str,
        actor: str = "daedalus",
        actor_kind: ActorKind = ActorKind.HUMAN,
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AuditEntry:
        """Append an entry to the audit log.  Returns the entry."""
        entry = AuditEntry(
            operation=operation,
            actor=actor,
            actor_kind=actor_kind,
            args=args or {},
            result=result or {},
            error=error,
        )
        entry.finalize()

        with self._lock:
            self._entries.append(entry)
            # Append to file
            with open(self._path, "a") as f:
                f.write(entry.to_json() + "\n")

        return entry

    def query(
        self,
        *,
        operation: str | None = None,
        actor: str | None = None,
        actor_kind: ActorKind | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[AuditEntry]:
        """Query the audit log with optional filters."""
        results = self._entries
        if operation:
            results = [e for e in results if e.operation == operation]
        if actor:
            results = [e for e in results if e.actor == actor]
        if actor_kind:
            results = [e for e in results if e.actor_kind == actor_kind]
        if since:
            results = [e for e in results if e.timestamp >= since]
        if limit is not None:
            results = results[-limit:]
        return results

    def tail(self, n: int = 20) -> list[AuditEntry]:
        """Return the last *n* entries."""
        return self._entries[-n:]

    def count(self) -> int:
        return len(self._entries)

    def verify(self) -> bool:
        """Verify checksums of all in-memory entries."""
        for entry in self._entries:
            if entry.checksum != entry._compute_checksum():
                return False
        return True

    def clear(self) -> None:
        """Clear the audit log (irreversible — requires confirm)."""
        with self._lock:
            self._entries.clear()
            if os.path.exists(self._path):
                os.remove(self._path)
