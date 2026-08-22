"""Integration tests — SPEC phase 3 §M7: the detonation loop, live.

The §M7 acceptance contract, run against the real container daemon (1.2.2):

* a file-dropping sample and a no-op sample produce **distinguishable**
  behavioral reports — the runtime write to ``/root/dropped.txt`` is
  captured by the pre/post filesystem export diff;
* both runs clean up after themselves: no ``detonation-*`` containers and
  no ``daedalus-detonation-*`` networks remain;
* a nonexistent sample fails cleanly with ``DetonationError`` and leaves no
  containers or networks behind.

Each detonation takes ~15–30 s live — boot-bound, because ``observe_seconds``
is a *cap* on the observation window, not a fixed duration: a sample that
finishes early (like these) completes the run the moment its exec returns.
The two-sample test is marked ``slow`` (~35–90 s for both runs).

The ``caps`` fixture (conftest) already ``pytest.skip``s the whole module
when the container binary or daemon is absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import DaedalusError, DetonationError
from daedalus.core.forge import Forge
from daedalus.core.profiles import ProfileRegistry
from daedalus.core.store import Store
from daedalus.core.talos import Talos

DETONATION_IMAGE = "alpine:latest"

# The two §M7 samples.  Both are plain sh, so ``sh /root/sample.sh`` runs
# them without any image-side tools; the dropper's runtime write to
# /root/dropped.txt is what the FS diff must catch.
DROPPER_SCRIPT = "#!/bin/sh\necho DROPPER-ACTIVE > /root/dropped.txt\nsleep 2\n"
NOOP_SCRIPT = "#!/bin/sh\nexit 0\n"


def _write_sample(path: Path, script: str) -> Path:
    """Write an executable shell sample; return its path."""
    path.write_text(script)
    path.chmod(0o755)
    return path


def _profile_kwargs() -> dict[str, Any]:
    """Resolve the detonation profile to run kwargs (SPEC phase 3 §3.2).

    The engine deliberately does NOT resolve ``profile`` itself — surfaces
    resolve it via ``ProfileRegistry`` and pass the kwargs.  The test mimics
    the CLI/API/MCP call pattern exactly.
    """
    return ProfileRegistry().get("detonation").apply()


async def _assert_no_detonation_leftovers(
    backend: CliBackend, caps: CapabilityManifest,
) -> None:
    """No ``detonation-*`` containers and no ``daedalus-detonation-*`` networks.

    BuildKit/plugin containers (``container list --all`` shows them) never
    match the ``detonation-`` name prefix, so filtering by prefix isolates
    exactly what the loop owns.
    """
    infos = await backend.list(all=True)
    leftovers = [i.name for i in infos if i.name.startswith("detonation-")]
    assert leftovers == [], f"detonation containers left behind: {leftovers}"
    if caps.networking is True:
        talos = Talos(backend, caps)
        networks = [
            n.name for n in await talos.network_list()
            if n.name.startswith("daedalus-detonation-")
        ]
        assert networks == [], f"detonation networks left behind: {networks}"


@pytest.mark.integration
@pytest.mark.slow
class TestDetonationDistinguishesActivity:
    """The §M7 test: two full detonations, distinguishable reports, no leaks."""

    async def test_detonation_distinguishes_activity(
        self,
        forge: Forge,
        backend: CliBackend,
        caps: CapabilityManifest,
        tmp_path: Path,
    ) -> None:
        if caps.networking is not True:
            pytest.skip("networking unavailable — detonation needs an internal network")

        dropper = _write_sample(tmp_path / "dropper.sh", DROPPER_SCRIPT)
        noop = _write_sample(tmp_path / "noop.sh", NOOP_SCRIPT)

        store = Store()
        known = {m.run_id for m in store.list()}
        try:
            dropper_result = await forge.detonate(
                str(dropper),
                image=DETONATION_IMAGE,
                observe_seconds=15.0,
                stats_interval=1.0,
                profile_kwargs=_profile_kwargs(),
            )
            noop_result = await forge.detonate(
                str(noop),
                image=DETONATION_IMAGE,
                observe_seconds=15.0,
                stats_interval=1.0,
                profile_kwargs=_profile_kwargs(),
            )

            # --- dropper: the runtime write IS captured -------------------
            assert dropper_result["exit_code"] == 0
            dropper_fs = dropper_result["report"]["filesystem"]
            assert dropper_fs["total_changes"] >= 2, (
                "dropper report missed changes: "
                f"added={dropper_fs['added']} modified={dropper_fs['modified']}"
            )
            assert "root/dropped.txt" in dropper_fs["added"], (
                "runtime write to /root/dropped.txt missing from FS diff: "
                f"added={dropper_fs['added']}"
            )

            # --- noop: only the delivered sample --------------------------
            assert noop_result["exit_code"] == 0
            noop_fs = noop_result["report"]["filesystem"]
            assert noop_fs["total_changes"] == 1, (
                "noop sample produced unexpected changes: "
                f"added={noop_fs['added']} modified={noop_fs['modified']}"
            )

            # --- the two reports are distinguishable ----------------------
            assert dropper_result["report"] != noop_result["report"]
            assert dropper_result["risk_score"] >= noop_result["risk_score"], (
                f"dropper risk {dropper_result['risk_score']} < noop risk "
                f"{noop_result['risk_score']}"
            )

            # --- evidence persisted (report file + store artifacts) -------
            report_path = Path(dropper_result["report_path"])
            assert report_path.is_file(), "report.json not written to the store"
            # The pre/post tars and the report are all store-indexed.  Note
            # each file is registered under several artifact names (icarus's
            # "filesystem.tar" per export, detonate's "filesystem.*.tar" and
            # "report.json", destroy's "behavioral_report.json") — the path
            # SET is the evidence contract, not the count.
            expected_artifacts = {
                str(Path(store.root) / f"{dropper_result['run_id']}.pre.tar"),
                str(Path(store.root) / f"{dropper_result['run_id']}.post.tar"),
                str(report_path),
            }
            assert set(dropper_result["artifacts"]) == expected_artifacts
            assert all(Path(p).is_file() for p in expected_artifacts)
            manifest = Store().get(dropper_result["run_id"])
            assert manifest is not None, "store manifest missing for detonated run"
            assert "report.json" in {_a_name(a) for a in manifest.artifacts}

            # --- cleanup: no VMs or networks survive the loop -------------
            await _assert_no_detonation_leftovers(backend, caps)
        finally:
            # Tidy the store: test run manifests are residue, not evidence
            # the operator asked to keep (the report/tar files stay on disk).
            # The snapshot catches even a run whose detonate() raised, so
            # no manifest is left behind by a mid-test failure.
            for manifest in store.list():
                if manifest.run_id not in known:
                    store.delete(manifest.run_id)


def _a_name(artifact: object) -> str:
    """Artifact name — dataclass in memory, dict when rehydrated from JSON."""
    if isinstance(artifact, dict):
        return str(artifact.get("name", ""))
    return str(getattr(artifact, "name", ""))


@pytest.mark.integration
class TestDetonationFailure:
    """A failed detonation cleans up after itself and re-raises with context."""

    async def test_detonation_missing_sample_fails_cleanly(
        self,
        forge: Forge,
        backend: CliBackend,
        caps: CapabilityManifest,
        tmp_path: Path,
    ) -> None:
        if caps.networking is not True:
            pytest.skip("networking unavailable — detonation needs an internal network")

        missing = tmp_path / "missing-sample.sh"
        store = Store()
        known = {m.run_id for m in store.list()}
        try:
            # The missing file fails at the delivery step (copy_to), which is
            # mid-loop → DetonationError.  A policy-gate rejection (e.g. the
            # concurrency cap) would surface as a plain DaedalusError instead.
            with pytest.raises((DetonationError, DaedalusError)):
                await forge.detonate(
                    str(missing),
                    image=DETONATION_IMAGE,
                    observe_seconds=5.0,
                    stats_interval=1.0,
                    profile_kwargs=_profile_kwargs(),
                )
            # The VM and disposable network were destroyed before re-raising.
            await _assert_no_detonation_leftovers(backend, caps)
        finally:
            # The failed run's manifest is not evidence worth keeping.
            for manifest in store.list():
                if manifest.run_id not in known:
                    store.delete(manifest.run_id)
