"""M1 — forge: lifecycle + system core.

Forge is the engine's central subsystem.  It wraps every lifecycle and system
command, owns the ``RunSpec`` object, and acts as the single source of truth
for container existence and state.

All operations flow through forge → backend, with policy checks and audit
applied *before* any backend call.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import (
    Backend,
    ContainerInfo,
    ExecResult,
    NetworkSpec,
    RunSpec,
    StatsSample,
)
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import (
    BackendTimeoutError,
    DaedalusError,
    DetonationError,
    ValidationError,
)
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.minos import BehavioralReport, Minos, stats_samples_to_snapshots
from daedalus.core.sampling import get_sampler
from daedalus.core.talos import Talos

# Detached with no command: image defaults exit immediately in the background.
DEFAULT_DETACHED_COMMAND: list[str] = ["tail", "-f", "/dev/null"]
from daedalus.core.policy import Decision, PolicyEngine
from daedalus.core.store import Artifact, Store

DAEDALUS_PROFILE_LABEL = "daedalus.profile"

# ==========================================================================
# Enriched container model
# ==========================================================================


@dataclass
class Labyrinth:
    """A container as forge sees it — a Labyrinth (sandbox VM)."""

    info: ContainerInfo
    profile: str = "default"
    labels: dict[str, str] = field(default_factory=dict)
    created_at: str = ""

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def image(self) -> str:
        return self.info.image

    @property
    def state(self) -> str:
        return self.info.state.value

    @property
    def is_running(self) -> bool:
        return self.info.state == self.info.state.RUNNING

    @property
    def is_stopped(self) -> bool:
        return self.info.state in (
            self.info.state.STOPPED, self.info.state.EXITED, self.info.state.CREATED,
        )


# ==========================================================================
# System status
# ==========================================================================


@dataclass
class SystemStatus:
    """Aggregated system view."""

    apiserver_running: bool = False
    container_version: str = ""
    container_commit: str = ""
    container_count: int = 0
    running_count: int = 0
    disk_usage: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)


# ==========================================================================
# Forge
# ==========================================================================


class Forge:
    """Lifecycle and system core of the DAEDALUS control plane.

    Parameters
    ----------
    backend:
        The active ``Backend`` implementation.
    capabilities:
        Host capability manifest.
    policy:
        Optional policy engine for pre-execution guardrails.
    audit:
        Optional audit log for operation recording.
    store:
        Optional run store for experiment manifests.
    """

    def __init__(
        self,
        backend: Backend,
        capabilities: CapabilityManifest,
        *,
        policy: PolicyEngine | None = None,
        audit: AuditLog | None = None,
        store: Store | None = None,
    ) -> None:
        self._backend = backend
        self._capabilities = capabilities
        self._policy = policy or PolicyEngine()
        self._audit = audit or AuditLog()
        self._store = store or Store()
        self._labyrinths: dict[str, Labyrinth] = {}

    @property
    def backend(self) -> Backend:
        return self._backend

    @property
    def capabilities(self) -> CapabilityManifest:
        return self._capabilities

    # ==================================================================
    # Lifecycle
    # ==================================================================

    async def create(
        self,
        image: str,
        *,
        name: str | None = None,
        profile: str = "default",
        command: list[str] | None = None,
        confirm_kernel: bool = False,
        **kwargs: Any,
    ) -> Labyrinth:
        """Create a container (does not start it)."""
        await self._check_run(image, confirm_kernel=confirm_kernel, **kwargs)
        if not command:
            command = list(DEFAULT_DETACHED_COMMAND)
        spec = RunSpec(
            image=image, name=name, command=command,
            **_filter_run_kwargs(_with_profile_label(kwargs, profile)),
        )
        info = await self._backend.create(spec)
        lab = Labyrinth(info=info, profile=profile, created_at=_now())
        self._labyrinths[lab.id] = lab
        self._audit.record(
            "create", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"image": image, "name": name, "profile": profile},
            result={"id": lab.id},
        )
        self._store.create(
            lab.id, image=image, image_digest="", profile=profile,
            command=command or [], container_name=lab.name,
        )
        return lab

    async def run(
        self,
        image: str,
        *,
        name: str | None = None,
        profile: str = "default",
        detach: bool = False,
        command: list[str] | None = None,
        confirm_kernel: bool = False,
        **kwargs: Any,
    ) -> Labyrinth:
        """Create and start a container."""
        await self._check_run(image, confirm_kernel=confirm_kernel, **kwargs)
        if not command:
            command = list(DEFAULT_DETACHED_COMMAND)
        spec = RunSpec(
            image=image, name=name, detach=detach, command=command,
            **_filter_run_kwargs(_with_profile_label(kwargs, profile)),
        )
        info = await self._backend.run(spec)
        lab = Labyrinth(info=info, profile=profile, created_at=_now())
        self._labyrinths[lab.id] = lab
        self._audit.record(
            "run", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"image": image, "name": name, "profile": profile},
            result={"id": lab.id},
        )
        self._store.create(
            lab.id, image=image, image_digest="", profile=profile,
            command=command or [], container_name=lab.name,
        )
        # Inject CA cert if specified (the container runtime only supports
        # directory volume mounts, so we exec the cert content in)
        cert_path = kwargs.get("cert_path")
        if cert_path:
            await _inject_cert(self._backend, lab.id, cert_path)
        return lab

    async def start(
        self,
        container_id: str,
        *,
        attach: bool = False,
        interactive: bool = False,
    ) -> Labyrinth:
        await self._backend.start(
            container_id, attach=attach, interactive=interactive,
        )
        lab = await self._refresh(container_id)
        self._audit.record(
            "start", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id},
            result={"id": lab.id, "state": lab.state},
        )
        return lab

    async def stop(
        self,
        container_id: str,
        timeout: int = 10,
        *,
        signal: str | None = None,
    ) -> Labyrinth:
        await self._backend.stop(container_id, timeout=timeout, signal=signal)
        lab = await self._refresh(container_id)
        self._audit.record(
            "stop", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "timeout": timeout},
        )
        return lab

    async def kill(self, container_id: str, signal: str = "KILL") -> Labyrinth:
        await self._backend.kill(container_id, signal=signal)
        lab = await self._refresh(container_id)
        self._audit.record(
            "kill", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "signal": signal},
        )
        return lab

    async def delete(self, container_id: str, force: bool = False) -> None:
        await self._backend.delete(container_id, force=force)
        self._labyrinths.pop(container_id, None)
        self._audit.record(
            "delete", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "force": force},
        )

    async def destroy(
        self,
        container_id: str,
        *,
        confirm: bool = False,
        report: Any = None,
    ) -> dict[str, Any] | None:
        """Destroy a Labyrinth: stop (if running) then delete.

        Requires explicit ``confirm=True`` (policy-gated).
        Returns a behavioral report dict when analysis completes.

        ``report``: an optional pre-built ``BehavioralReport`` (or anything
        with ``to_dict()``) written as-is instead of the thinner logs-only
        re-analysis — phase 3 ``detonate`` passes its rich report here so it
        cannot be overwritten by a report derived from logs alone.
        """
        r = self._policy.check_destroy(confirm=confirm)
        if r.decision == Decision.CONFIRM:
            raise ValidationError("destroy requires confirm=True")
        self._policy.enforce(r)

        manifest = self._store.get(container_id)
        boot_log = ""
        process_logs = ""
        try:
            boot_log = await self._backend.logs(container_id, boot=True)
            process_logs = await self._backend.logs(container_id)
        except Exception:
            pass

        try:
            await self._backend.stop(container_id, timeout=5)
        except Exception:
            pass
        await self._backend.delete(container_id, force=True)
        self._labyrinths.pop(container_id, None)
        self._audit.record(
            "destroy", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "confirm": True},
        )

        report_data: dict[str, Any] | None = None
        try:
            if report is not None:
                # Phase 3: detonate passes its rich report — write it as-is
                # rather than re-analyzing from logs alone (which would
                # produce a thinner report and overwrite the rich one).
                report_data = report.to_dict()
            else:
                logs_report = await Minos(self._backend).analyze(
                    container_id,
                    image=manifest.image if manifest else "",
                    image_digest=manifest.image_digest if manifest else "",
                    boot_log=boot_log or process_logs,
                )
                report_data = logs_report.to_dict()
            report_path = os.path.join(
                self._store.root, f"{container_id}.report.json",
            )
            with open(report_path, "w") as f:
                f.write(json.dumps(report_data, indent=2))
            self._store.add_artifact(
                container_id,
                Artifact(
                    name="behavioral_report.json",
                    path=report_path,
                    kind="report",
                    description="Minos behavioral report",
                ),
            )
        except Exception:
            report_data = None

        self._store.update(container_id, exit_code=0)
        return report_data

    async def detonate(
        self,
        sample: str,
        *,
        image: str = "alpine:latest",
        profile: str = "detonation",
        name: str | None = None,
        command: list[str] | None = None,
        observe_seconds: float = 30.0,
        stats_interval: float = 2.0,
        network_name: str | None = None,
        profile_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Detonate a sample in a disposable, isolated Labyrinth.

        The full loop (SPEC phase 3 §3.2): policy gate (incl. the container
        1.3.1 runtime-security floor — an unpatched runtime hard-blocks here)
        → disposable internal network → keep-alive run → pre-export baseline
        → sample delivery →
        concurrent exec + stats sampling → post-evidence (logs + post-export)
        → judge (Minos) → persist (report file + store artifacts + audit) →
        destroy with the rich report.  The network is always deleted in a
        ``finally`` — no leak on aborted runs.

        ``profile`` is used for the run label and the audit entry only — it
        is NOT resolved to kwargs here.  Surfaces resolve the profile via
        ``ProfileRegistry`` and pass the resulting kwargs as
        ``profile_kwargs`` (consistent with ``run``/``create``).

        The sample is copied into the container as ``/root/sample<ext>``
        and executed via ``sh`` (or the explicit ``command``) while stats
        are sampled every ``stats_interval`` seconds (floored at 0.1 s).
        Exec timing out at the observation boundary is normal completion —
        the run was observed for the full ``observe_seconds`` window; the
        reported ``exit_code`` is then the conventional timeout code 124.

        **Delivery path is /root, never /tmp**: profiles that mount tmpfs
        on ``/tmp`` shadow the disk layer there, so a file copied into
        ``/tmp`` is invisible to ``exec`` (the sample would never run) —
        and tmpfs writes are invisible to the post-run export, which would
        blind the filesystem-delta evidence that is the whole point of the
        report.

        **Stats evidence path**: the inline raw ``StatsSample`` loop below
        remains the *evidence* source — minos's
        ``stats_samples_to_snapshots`` consumes cumulative raw samples
        with pairwise ``cpu_percent``, which two-tick derived rates cannot
        reproduce.  The shared per-process ``StatsSampler``
        (``get_sampler``) runs *alongside* it for the observation window
        so the run's stats also land in the global history ring (the live
        UI spine); it is started before the exec and stopped in the
        ``finally`` when the exec completes (phase 5, W1).

        Returns
        -------
        dict with ``run_id``, ``report`` (``BehavioralReport.to_dict()``),
        ``report_path``, ``artifacts`` (store artifact paths), ``risk_score``,
        and ``exit_code``.

        Raises
        ------
        DetonationError
            If the loop fails partway through.  The VM and network are
            destroyed first (best-effort); ``partial_evidence`` carries the
            pre/post tars and report path that were actually written.
        """
        kwargs = dict(profile_kwargs or {})

        # Step 1 — policy gate (concurrency / disk / image), same as run/create.
        await self._check_run(image, **kwargs)

        # Step 1b — runtime security floor.  Detonation's isolation promise
        # depends on the container 1.3.1 GHSA fixes (bundle file-deletion via
        # unchecked ids, OCI descriptor path traversal, symlink reads on image
        # load, registry realm validation, crafted-layer crashes).  There is no
        # override — upgrading the runtime is the fix.
        r = self._policy.check_runtime_security(
            security_safe=self._capabilities.security_safe,
        )
        if r.decision == Decision.CONFIRM:
            raise ValidationError(
                "detonation requires a patched container runtime (>= 1.3.1) "
                "— GHSA advisories unfixed; upgrade container"
            )
        self._policy.log("check_runtime_security", "forge", r)
        self._policy.enforce(r)

        run_name = name or (
            f"detonation-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
            f"-{uuid.uuid4().hex[:8]}"
        )
        talos = Talos(
            self._backend, self._capabilities,
            policy=self._policy, audit=self._audit,
        )

        # Step 2 — disposable internal network (always cleaned up in finally).
        if network_name is None:
            network = f"daedalus-detonation-{run_name}"
            try:
                await talos.network_create(NetworkSpec(name=network, internal=True))
            except DaedalusError as exc:
                # A same-name network already exists — reuse it.
                if "already" not in (str(exc) + str(exc.details)).lower():
                    raise
        else:
            network = network_name

        run_id = ""
        partial_evidence: dict[str, Any] = {}
        try:
            # Step 3 — keep-alive run (NOT the sample yet).
            lab = await self.run(
                image,
                name=run_name,
                profile=profile,
                detach=True,
                network=network,
                command=list(DEFAULT_DETACHED_COMMAND),
                **kwargs,
            )
            run_id = lab.id
            icarus = Icarus(
                self._backend, audit=self._audit, store=self._store,
            )
            pre_path = os.path.join(self._store.root, f"{run_id}.pre.tar")
            post_path = os.path.join(self._store.root, f"{run_id}.post.tar")
            report_path = os.path.join(self._store.root, f"{run_id}.report.json")

            # Step 4 — baseline filesystem export.
            await icarus.export_fs(run_id, output_path=pre_path)
            partial_evidence["pre_tar"] = pre_path

            # Step 5 — deliver the sample, preserving its extension.
            # /root (not /tmp): tmpfs mounts shadow /tmp for exec AND hide
            # runtime writes from the export diff (see the method docstring).
            suffix = os.path.splitext(os.path.basename(sample))[1] or ".bin"
            dest = f"/root/sample{suffix}"
            await icarus.copy_to(run_id, sample, dest)

            # Step 6 — detonate + observe concurrently: exec the sample while
            # a sampler drains stats into ``samples`` until exec completes.
            # The shared per-process StatsSampler also runs (started before
            # the exec, stopped in the finally below) so the run's stats land
            # in the global history ring — see the method docstring for why
            # it is NOT the evidence source.
            get_sampler(self._backend).start()
            sample_cmd = command or ["sh", dest]
            samples: list[StatsSample] = []
            exec_task = asyncio.create_task(
                icarus.exec(
                    run_id, sample_cmd,
                    options=ExecOptions(timeout=observe_seconds),
                ),
            )
            sampler_task = asyncio.create_task(
                self._detonation_sampler(run_id, stats_interval, samples),
            )
            try:
                try:
                    exec_result = await exec_task
                except BackendTimeoutError:
                    # The sample ran the full observation window — normal
                    # completion.  The runtime killed the exec session; use
                    # the conventional "timed out" exit code.
                    exec_result = ExecResult(exit_code=124, stdout="", stderr="")
            finally:
                sampler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sampler_task
                get_sampler(self._backend).stop()

            # Step 7 — post-evidence: boot + process logs, post-export.
            boot_log = await icarus.logs(run_id, boot=True)
            process_logs = await icarus.logs(run_id)
            await icarus.export_fs(run_id, output_path=post_path)
            partial_evidence["post_tar"] = post_path

            # Step 8 — judge: pre/post FS diff + stats snapshots + logs.
            minos = Minos(self._backend)
            report: BehavioralReport = await minos.analyze(
                run_id,
                image=image,
                pre_tar=pre_path,
                post_tar=post_path,
                stats_data=stats_samples_to_snapshots(samples),
                boot_log=f"{boot_log}\n{process_logs}",
            )
            report_dict = report.to_dict()

            # Step 9 — persist: report file, store artifacts, audit.
            with open(report_path, "w") as f:
                f.write(json.dumps(report_dict, indent=2))
            partial_evidence["report_path"] = report_path
            self._store.add_artifact(
                run_id,
                Artifact(
                    name="report.json", path=report_path, kind="report",
                    description="Minos behavioral report (detonation)",
                ),
            )
            self._store.add_artifact(
                run_id,
                Artifact(
                    name="filesystem.pre.tar", path=pre_path, kind="fs",
                    description="Filesystem export before detonation",
                ),
            )
            self._store.add_artifact(
                run_id,
                Artifact(
                    name="filesystem.post.tar", path=post_path, kind="fs",
                    description="Filesystem export after detonation",
                ),
            )
            self._audit.record(
                "detonate", actor="forge", actor_kind=ActorKind.SERVICE,
                args={
                    "sample": sample, "image": image, "profile": profile,
                    "observe_seconds": observe_seconds,
                },
                result={
                    "run_id": run_id,
                    "risk_score": report.risk_score,
                    "fs_changes": report.filesystem.total_changes,
                },
            )

            # Step 10 — destroy with the rich report (no thinner re-analysis).
            await self.destroy(run_id, confirm=True, report=report)

            manifest = self._store.get(run_id)
            return {
                "run_id": run_id,
                "report": report_dict,
                "report_path": report_path,
                "artifacts": [a.path for a in manifest.artifacts] if manifest else [],
                "risk_score": report.risk_score,
                "exit_code": exec_result.exit_code,
            }
        except Exception as exc:
            # Cleanup first (best-effort), then re-raise with context.
            if run_id:
                with contextlib.suppress(Exception):
                    await self.destroy(run_id, confirm=True)
            raise DetonationError(
                f"Detonation failed for sample '{sample}'"
                + (f" (run {run_id})" if run_id else ""),
                partial_evidence=partial_evidence,
            ) from exc
        finally:
            if network is not None:
                with contextlib.suppress(Exception):
                    await talos.network_delete(network, confirm=True)

    async def _detonation_sampler(
        self, run_id: str, interval: float, sink: list[StatsSample],
    ) -> None:
        """Sample container stats every ``interval`` seconds into ``sink``.

        Runs until cancelled — the detonate loop cancels it when the sample
        exec completes.  The interval is floored at 0.1 s so a zero or
        negative ``stats_interval`` cannot busy-loop the backend.
        """
        interval = max(interval, 0.1)
        while True:
            await asyncio.sleep(interval)
            sampled = await self.stats(run_id)
            if sampled:
                sink.append(sampled[0])

    async def list(self, all: bool = False) -> list[Labyrinth]:
        infos = await self._backend.list(all=all)
        labs = []
        for info in infos:
            if info.id in self._labyrinths:
                lab = self._labyrinths[info.id]
                lab.info = info
            else:
                lab = Labyrinth(info=info)
                self._labyrinths[info.id] = lab
            if lab.info.state == lab.info.state.RUNNING:
                with contextlib.suppress(Exception):
                    lab.info = await self._backend.inspect(lab.id)
            lab.profile = self._resolve_profile(lab)
            labs.append(lab)
        return labs

    async def inspect(self, container_id: str) -> Labyrinth:
        info = await self._backend.inspect(container_id)
        lab = Labyrinth(info=info)
        lab.profile = self._resolve_profile(lab)
        self._labyrinths[container_id] = lab
        return lab

    async def get(self, container_id: str) -> Labyrinth:
        if container_id in self._labyrinths:
            return self._labyrinths[container_id]
        return await self.inspect(container_id)

    def _resolve_profile(self, lab: Labyrinth) -> str:
        """Resolve profile from container labels, store, or forge memory."""
        from_label = _profile_from_raw(lab.info.raw)
        if from_label:
            return from_label
        manifest = self._store.get(lab.id)
        if manifest and manifest.profile:
            return manifest.profile
        if lab.profile not in ("", "default"):
            return lab.profile
        return "external"

    # ==================================================================
    # System
    # ==================================================================

    async def stats(self, container_id: str | None = None) -> builtins.list[StatsSample]:
        """One-shot resource statistics for a container (or all running).

        Read-only and high-frequency, so this is a pure passthrough to
        the backend: no policy checks are applied and no audit entry is
        recorded (spec §4.4).  ``container_id`` may be omitted to sample
        all running containers.

        (``builtins.list``: the bare ``list`` annotation inside this class
        body resolves to ``Forge.list`` — see backend.py for the same
        convention.)
        """
        return await self._backend.stats(container_id)

    async def system_status(self) -> SystemStatus:
        disk = await self._backend.system_df()
        try:
            all_containers = await self._backend.list(all=True)
            total = len(all_containers)
            running = sum(
                1 for c in all_containers if c.state == c.state.RUNNING
            )
        except Exception:
            total = 0
            running = 0

        return SystemStatus(
            apiserver_running=bool(self._capabilities.apiserver_running),
            container_version=self._capabilities.container_version,
            container_commit=self._capabilities.container_commit,
            container_count=total,
            running_count=running,
            disk_usage=disk,
            capabilities=self._capabilities.as_dict(),
        )

    # ==================================================================
    # Internals
    # ==================================================================

    async def _check_run(
        self,
        image: str,
        *,
        confirm_kernel: bool = False,
        **kwargs: Any,
    ) -> None:
        """Run policy checks before creating/starting a container."""
        r = self._policy.check_image(image)
        self._policy.log("check_image", "forge", r)
        self._policy.enforce(r)

        running_count = sum(
            1 for lab in self._labyrinths.values() if lab.is_running
        )
        r = self._policy.check_concurrency(running_count)
        self._policy.log("check_concurrency", "forge", r)
        self._policy.enforce(r)

        disk = await self._backend.system_df()
        r = self._policy.check_disk(disk)
        self._policy.log("check_disk", "forge", r)
        self._policy.enforce(r)

        kernel = kwargs.get("kernel")
        if kernel:
            r = self._policy.check_kernel_change(confirm=confirm_kernel)
            if r.decision == Decision.CONFIRM:
                raise ValidationError("kernel change requires confirm_kernel=True")
            self._policy.log("check_kernel_change", "forge", r)
            self._policy.enforce(r)

    async def _refresh(self, container_id: str) -> Labyrinth:
        info = await self._backend.inspect(container_id)
        if container_id in self._labyrinths:
            self._labyrinths[container_id].info = info
        else:
            self._labyrinths[container_id] = Labyrinth(info=info)
        return self._labyrinths[container_id]


def _now() -> str:
    return datetime.now(UTC).isoformat()


# RunSpec constructor fields — used to filter kwargs
_RUNSPEC_FIELDS = {
    "image", "name", "detach", "remove", "workdir", "env", "env_file",
    "entrypoint", "user", "uid", "gid", "interactive", "tty", "cpus",
    "memory", "mounts", "tmpfs", "volumes", "kernel", "labels",
    "cidfile", "os", "arch", "dns", "dns_domain", "dns_search", "dns_option", "no_dns",
    "scheme", "disable_progress_updates", "command",
    "proxy", "cert_path", "no_proxy",
    "network", "publish", "cap_add", "cap_drop",
    "read_only", "init_image", "ssh",
    "init", "kernel_arg", "masked_path", "read_only_path", "rosetta",
    "runtime", "shm_size", "ulimit", "publish_socket", "virtualization",
    "platform",
}


def _filter_run_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter kwargs to only include valid RunSpec fields."""
    return {k: v for k, v in kwargs.items() if k in _RUNSPEC_FIELDS}


async def _inject_cert(backend: Backend, container_id: str, cert_path: str) -> None:
    """Inject a CA certificate into a running container via exec.

    Handles both Debian/Ubuntu (/usr/local/share/ca-certificates/) and
    Alpine (/usr/local/share/ca-certificates/) paths.  The cert is
    written and update-ca-certificates is called if available.
    """
    import os as _os
    if not _os.path.exists(cert_path):
        return
    with open(cert_path) as f:
        cert_content = f.read()
    # Escape single quotes for shell
    escaped = cert_content.replace("'", "'\"'\"'")
    script = (
        "mkdir -p /usr/local/share/ca-certificates /etc/ssl/certs && "
        f"echo '{escaped}' > /usr/local/share/ca-certificates/daedalus-ca.crt && "
        f"cp /usr/local/share/ca-certificates/daedalus-ca.crt /etc/ssl/certs/daedalus-ca.pem 2>/dev/null; "
        "update-ca-certificates 2>/dev/null || "
        "update-ca-trust 2>/dev/null || "
        "c_rehash /etc/ssl/certs 2>/dev/null || true"
    )
    await backend.exec(container_id, ["sh", "-c", script])


def _with_profile_label(kwargs: dict[str, Any], profile: str) -> dict[str, Any]:
    labels = dict(kwargs.get("labels") or {})
    labels[DAEDALUS_PROFILE_LABEL] = profile
    return {**kwargs, "labels": labels}


def _profile_from_raw(raw: dict[str, Any]) -> str | None:
    if not raw:
        return None
    cfg = raw.get("configuration", raw)
    if not isinstance(cfg, dict):
        return None
    labels = cfg.get("labels", {})
    if isinstance(labels, dict) and DAEDALUS_PROFILE_LABEL in labels:
        return str(labels[DAEDALUS_PROFILE_LABEL])
    return None
