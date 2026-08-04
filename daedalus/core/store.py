"""Experiment and run metadata store with artifact index.

Every DAEDALUS run is reproducible from a store manifest:

* Image digest (which image, exact version)
* Profile (security posture applied)
* Network topology
* Kernel image
* Init-image
* Command arguments
* Artifacts produced (tars, pcaps, reports)

The store is the foundation for reproducibility — given a manifest,
an identical run can be recreated.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Artifact:
    """An artifact produced during a run."""
    name: str           # e.g. "filesystem.tar", "report.json", "capture.pcap"
    path: str           # absolute path on host
    kind: str           # "tar", "json", "pcap", "log", "report", "other"
    size_bytes: int = 0
    checksum: str = ""  # sha256
    description: str = ""


@dataclass
class RunManifest:
    """Complete description of a run — everything needed to reproduce it."""

    run_id: str
    created_at: str = ""

    # What ran
    image: str = ""
    image_digest: str = ""

    # How it ran
    profile: str = "detonation"
    kernel: str | None = None
    init_image: str | None = None

    # Where it ran
    network_topology: str | None = None   # name of the topology
    dns_config: dict[str, Any] = field(default_factory=dict)

    # What it did
    command: list[str] = field(default_factory=list)
    container_name: str = ""

    # What it produced
    artifacts: list[Artifact] = field(default_factory=list)

    # Result
    exit_code: int | None = None
    duration_seconds: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


class Store:
    """Experiment and run metadata store.

    Parameters
    ----------
    root:
        Root directory for the store.  Defaults to ``~/.daedalus/store/``.
    """

    def __init__(self, root: str = "~/.daedalus/store/") -> None:
        self._root = os.path.expanduser(root)
        self._manifests: dict[str, RunManifest] = {}
        os.makedirs(self._root, exist_ok=True)

    @property
    def root(self) -> str:
        return self._root

    # ==================================================================
    # Manifest CRUD
    # ==================================================================

    def create(self, run_id: str, **kwargs: Any) -> RunManifest:
        manifest = RunManifest(
            run_id=run_id,
            created_at=datetime.now(UTC).isoformat(),
            **kwargs,
        )
        self._manifests[run_id] = manifest
        self._persist(manifest)
        return manifest

    def get(self, run_id: str) -> RunManifest | None:
        # Check cache
        if run_id in self._manifests:
            return self._manifests[run_id]
        # Check disk
        path = self._manifest_path(run_id)
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            manifest = RunManifest(**data)
            self._manifests[run_id] = manifest
            return manifest
        return None

    def list(self) -> list[RunManifest]:
        manifests = []
        for entry in os.listdir(self._root):
            if entry.endswith(".manifest.json"):
                run_id = entry[:-len(".manifest.json")]
                m = self.get(run_id)
                if m:
                    manifests.append(m)
        return sorted(manifests, key=lambda m: m.created_at, reverse=True)

    def update(self, run_id: str, **kwargs: Any) -> RunManifest | None:
        manifest = self.get(run_id)
        if not manifest:
            return None
        for k, v in kwargs.items():
            if hasattr(manifest, k):
                setattr(manifest, k, v)
        self._persist(manifest)
        return manifest

    def delete(self, run_id: str) -> bool:
        """Delete a run manifest. Returns True if deleted."""
        path = self._manifest_path(run_id)
        self._manifests.pop(run_id, None)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def clear(self) -> int:
        """Delete all run manifests. Returns count removed."""
        count = 0
        for entry in list(os.listdir(self._root)):
            if entry.endswith(".manifest.json"):
                run_id = entry[:-len(".manifest.json")]
                path = os.path.join(self._root, entry)
                os.remove(path)
                self._manifests.pop(run_id, None)
                count += 1
        return count

    def add_artifact(self, run_id: str, artifact: Artifact) -> None:
        manifest = self.get(run_id)
        if manifest:
            manifest.artifacts.append(artifact)
            self._persist(manifest)

    # ==================================================================
    # Internals
    # ==================================================================

    def _manifest_path(self, run_id: str) -> str:
        return os.path.join(self._root, f"{run_id}.manifest.json")

    def _persist(self, manifest: RunManifest) -> None:
        path = self._manifest_path(manifest.run_id)
        with open(path, "w") as f:
            f.write(manifest.to_json())
