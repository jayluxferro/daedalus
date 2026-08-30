"""Integration tests — SPEC phase 7 W4: the 1.3.1 run surface, live.

Live round-trips against the ``container`` daemon (1.3.1) for the
surface-completion workstream (SPEC phase 7 §7 W4):

* **multi-arch guests** — ``run --arch x86_64`` on Apple silicon is THE
  phase acceptance (a daedalus ``exec`` inside the guest sees
  ``uname -m`` → ``x86_64``);
* **new RunSpec flags reflected in the container config JSON** —
  ``--rosetta`` / ``--shm-size`` / ``--ulimit`` / ``--init`` land in
  ``container inspect`` (``rosetta``, ``shmSize``, ``useInit``, the
  ``RLIMIT_NOFILE`` rlimit);
* **exec wire order** — exec option flags must precede the container id
  (``exec --workdir /tmp <id> …``); anything after <id> is treated as the
  executable, so emitting options after the id is a hard regression;
* **bulk ``stop -a`` / ``delete -a``** — both stop and delete all
  containers on the host;
* **``--kernel-arg`` / ``--virtualization``** — accepted by the runtime
  and reflected in inspect (``virtualization`` appears in configuration).

Every container created here is destroyed in a ``finally`` with
``confirm=True``; the skip-when-no-container gate comes from the session
``caps`` fixture (``tests/integration/conftest.py`` → ``_skip_if_no_container``).
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from daedalus.core.backend import ContainerState
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import PolicyViolationError
from daedalus.core.forge import Forge, Labyrinth
from daedalus.core.store import Store

TEST_IMAGE = "alpine:latest"


def _skip_on_policy(exc: BaseException) -> None:
    if isinstance(exc, PolicyViolationError) or "POLICY" in str(exc):
        pytest.skip("disk policy blocked container create on this host")


async def _lab_run(
    forge: Forge,
    *,
    image: str = TEST_IMAGE,
    detach: bool = True,
    command: list[str] | None = None,
    profile_kwargs: dict[str, Any] | None = None,
    **run_kwargs: Any,
) -> Labyrinth:
    """Run a detached container through forge; skip when host policy blocks it.

    ``profile_kwargs`` are merged into the run kwargs first (the
    profile-resolved surface pattern), then explicit overrides win —
    matching how the CLI/API/MCP surfaces call ``forge.run``.
    """
    kwargs: dict[str, Any] = dict(profile_kwargs or {})
    kwargs.update(run_kwargs)
    try:
        return await forge.run(image, detach=detach, command=command, **kwargs)
    except Exception as exc:  # noqa: BLE001 — surface any policy block as a skip
        _skip_on_policy(exc)
        raise


@pytest.mark.integration
class TestRunSurface:
    """Phase-7 run-surface round-trips against the live daemon (1.3.1)."""

    async def test_multiarch_x86_64_roundtrip(
        self, forge: Forge, backend: CliBackend,
    ) -> None:
        """THE phase acceptance as a permanent regression test.

        ``daedalus run --arch x86_64`` on Apple silicon: the guest runs as
        amd64 and a daedalus exec round-trip sees ``uname -m`` == ``x86_64``.
        """
        lab = await _lab_run(
            forge,
            image=TEST_IMAGE,
            detach=True,
            arch="x86_64",
            command=["sleep", "300"],
            profile_kwargs={"no_dns": True},
        )
        cid = lab.id
        try:
            result = await backend.exec(cid, ["uname", "-m"])
            assert result.exit_code == 0, f"exec uname failed: {result.stderr!r}"
            assert result.stdout.strip() == "x86_64", (
                f"guest reports arch {result.stdout.strip()!r}, expected x86_64"
            )
        finally:
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)

    async def test_rosetta_and_limits_reflected(
        self, forge: Forge, backend: CliBackend,
    ) -> None:
        """New RunSpec flags round-trip into the container config JSON.

        ``--rosetta --shm-size 64M --ulimit nofile=1024 --init`` land in
        ``container inspect``'s ``configuration``: ``rosetta: true``,
        ``shmSize: 67108864``, ``useInit: true`` and an ``RLIMIT_NOFILE``
        rlimit entry with soft/hard 1024.
        """
        lab = await _lab_run(
            forge,
            rosetta=True,
            arch="x86_64",
            shm_size="64M",
            ulimit=["nofile=1024"],
            init=True,
        )
        cid = lab.id
        try:
            raw = (await backend.inspect(cid)).raw
            cfg = raw.get("configuration", {})
            if not isinstance(cfg, dict):
                cfg = {}
            assert cfg.get("rosetta") is True, f"rosetta not reflected: {cfg!r}"
            assert cfg.get("shmSize") == 67108864, (
                f"shmSize is {cfg.get('shmSize')!r}, expected 67108864"
            )
            assert cfg.get("useInit") is True, f"useInit not reflected: {cfg!r}"
            rlimits = cfg.get("initProcess", {}).get("rlimits", [])
            if not isinstance(rlimits, list):
                rlimits = []
            nofile = next(
                (r for r in rlimits if isinstance(r, dict) and r.get("limit") == "RLIMIT_NOFILE"),
                None,
            )
            assert nofile is not None, f"no RLIMIT_NOFILE rlimit in {rlimits!r}"
            assert nofile.get("soft") == 1024 and nofile.get("hard") == 1024, (
                f"RLIMIT_NOFILE is {nofile!r}, expected soft/hard 1024"
            )
        finally:
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)

    async def test_exec_options_precede_id(
        self, forge: Forge, backend: CliBackend,
    ) -> None:
        """Exec option flags precede the container id (wire-order regression).

        container ≥ 1.x treats every argument after <id> as the executable
        and its arguments, so ``exec --workdir /tmp <id> sh -c …`` is the
        only valid shape; emitting options after the id fails with "target
        executable" errors.
        """
        lab = await _lab_run(forge)
        cid = lab.id
        try:
            result = await backend.exec(
                cid, ["sh", "-c", "echo W=$PWD"], workdir="/tmp",
            )
            assert result.exit_code == 0, f"exec failed: {result.stderr!r}"
            assert "W=/tmp" in result.stdout, (
                f"workdir not applied: stdout {result.stdout!r}"
            )
        finally:
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)

    async def test_stop_all_and_delete_all(
        self, forge: Forge, backend: CliBackend,
    ) -> None:
        """Bulk ``stop -a`` / ``delete -a`` lifecycle on the live daemon.

        Two detached containers; ``backend.stop("", all=True)`` stops both
        (``list(all=True)`` shows ``stopped``), then
        ``backend.delete("", all=True)`` removes them.

        NOTE: these are host-wide bulk verbs — they act on **every**
        container, not just the two created here.  The cleanup in ``finally``
        is best-effort per-id (idempotent): the containers may already be
        gone when it runs.
        """
        cids: list[str] = []
        try:
            cids.append((await _lab_run(forge)).id)
            cids.append((await _lab_run(forge)).id)

            await backend.stop("", all=True)
            states = {c.id: c.state for c in await backend.list(all=True)}
            for cid in cids:
                assert states.get(cid) == ContainerState.STOPPED, (
                    f"{cid} state is {states.get(cid)!r} after stop -a"
                )

            await backend.delete("", all=True)
            remaining = {c.id for c in await backend.list(all=True)}
            survivors = set(cids) & remaining
            assert not survivors, (
                f"containers survived delete -a: {sorted(survivors)}"
            )
        finally:
            for cid in cids:
                with contextlib.suppress(Exception):
                    await forge.destroy(cid, confirm=True)
                Store().delete(cid)

    async def test_kernel_arg_virtualization_wire(
        self, forge: Forge, backend: CliBackend,
    ) -> None:
        """``--kernel-arg`` + ``--virtualization`` are accepted by the runtime.

        The run succeeds, the container runs a command, and inspect shows
        ``virtualization: true`` in ``configuration``.  (The runtime does
        not re-emit kernel args in inspect output on 1.3.1 — the wire
        contract is acceptance without error.)
        """
        lab = await _lab_run(
            forge,
            kernel_arg=["console=tty0"],
            virtualization=True,
        )
        cid = lab.id
        try:
            result = await backend.exec(cid, ["echo", "up"])
            assert result.exit_code == 0, f"container did not run: {result.stderr!r}"
            raw = (await backend.inspect(cid)).raw
            cfg = raw.get("configuration", {})
            if not isinstance(cfg, dict):
                cfg = {}
            assert cfg.get("virtualization") is True, (
                f"virtualization not reflected in config: {cfg!r}"
            )
        finally:
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)
