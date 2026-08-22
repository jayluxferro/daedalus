"""M8b — HTTP/REST API for services and web UIs.

Resource-oriented REST API over the Core Engine.  Includes SSE streaming
for live logs and system events, WebSocket for interactive terminals,
and static file serving for the Labyrinth Control Center UI.

Endpoints::
    GET  /health
    # Containers
    GET  /containers
    POST /containers
    GET  /containers/{id}
    DELETE /containers/{id}
    POST /containers/{id}/stop
    POST /containers/{id}/exec
    GET  /containers/{id}/logs
    GET  /containers/{id}/logs/stream  (SSE)
    WS   /containers/{id}/exec         (WebSocket interactive terminal)
    GET  /containers/{id}/stats
    POST /containers/{id}/copy
    POST /containers/{id}/export
    POST /containers/prune
    GET  /stats

    # Networks
    GET  /networks
    POST /networks
    GET  /networks/{name}
    DELETE /networks/{name}

    # Images
    GET  /images
    POST /images/pull
    GET  /images/{name}
    DELETE /images/{name}

    # Machines
    GET  /machines
    POST /machines
    GET  /machines/{machine_id}
    POST /machines/{machine_id}/set
    POST /machines/{machine_id}/set-default
    POST /machines/{machine_id}/stop
    DELETE /machines/{machine_id}
    POST /machines/{machine_id}/run
    GET  /machines/{machine_id}/logs

    # Profiles
    GET  /profiles

    # Detonation
    POST /detonate

    # Experiments
    GET  /experiments
    GET  /experiments/{run_id}
    GET  /experiments/{run_id}/report
    DELETE /experiments/{run_id}
    DELETE /experiments

    # System
    GET  /system/status
    GET  /system/audit
    GET  /system/events               (SSE)

    # UI (static SPA)
    GET  /ui/*
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Literal

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import (
    BuildSpec,
    MachineCreateSpec,
    MachineSetSettings,
    NetworkSpec,
)
from daedalus.core.capabilities import ensure_daemon, probe
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import DaedalusError
from daedalus.core.forge import Forge
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.machina import Machina
from daedalus.core.mint import Mint
from daedalus.core.policy import PolicyEngine, PolicyResult, load_policy_config
from daedalus.core.profiles import ProfileRegistry
from daedalus.core.store import Store
from daedalus.core.talos import Talos

# ==========================================================================
# Request models
# ==========================================================================


class RunRequest(BaseModel):
    image: str
    name: str | None = None
    profile: str = "detonation"
    start: bool = True  # False = create only (stopped); True = create and run
    detach: bool = True
    remove: bool = False  # keep container record unless caller opts into --rm
    command: list[str] | None = None
    kernel: str | None = None
    cpus: int | None = None
    memory: str | None = None
    dns: list[str] | None = None
    volumes: list[str] | None = None
    mounts: list[str] | None = None
    proxy: str | None = None
    cert_path: str | None = None
    no_proxy: str | None = None
    env: dict[str, str] | None = None
    workdir: str | None = None
    confirm_kernel: bool = False
    network: str | None = None       # --network <name> (attach)
    publish: list[str] = []          # -p spec, repeatable
    cap_add: list[str] = []          # --cap-add
    cap_drop: list[str] = []         # --cap-drop


class ImageTagRequest(BaseModel):
    source: str
    target: str


class RegistryLoginRequest(BaseModel):
    server: str
    username: str | None = None
    password: str | None = None
    scheme: str | None = None


class RegistryDefaultRequest(BaseModel):
    host: str
    scheme: str | None = None


class SystemKernelSetRequest(BaseModel):
    binary: str | None = None
    tar: str | None = None
    arch: str = "arm64"
    recommended: bool = False


class MachineCreateRequest(BaseModel):
    image: str
    name: str | None = None
    cpus: int | None = None
    memory: str | None = None
    home_mount: str | None = None
    virtualization: bool = False
    kernel: str | None = None
    set_default: bool = False
    no_boot: bool = False


class MachineSetRequest(BaseModel):
    cpus: int | None = None
    memory: str | None = None
    home_mount: str | None = None
    virtualization: bool | None = None
    kernel: str | None = None


class MachineRunRequest(BaseModel):
    command: list[str]


def _container_dict(lab: Any) -> dict[str, Any]:
    return {
        "id": lab.id,
        "name": lab.name,
        "image": lab.image,
        "state": lab.state,
        "profile": lab.profile,
        "ip": "",
        "networks": [],
    }


def _network_dict(net: Any) -> dict[str, Any]:
    return {
        "id": net.id,
        "name": net.name,
        "plugin": net.plugin,
        "mode": net.mode,
        "subnet": net.subnet,
        "subnet_v6": net.subnet_v6,
        "gateway": net.gateway,
    }


def _machine_dict(info: Any) -> dict[str, Any]:
    """Serialise a machine (``MachineInfo``) as a flat dict.

    ``to_dict()`` includes the engine-set advisor ``warning`` field.
    """
    return info.to_dict()


class ExecRequest(BaseModel):
    command: list[str]
    user: str | None = None
    uid: int | None = None
    gid: int | None = None
    workdir: str | None = None
    env: dict[str, str] | None = None
    env_file: str | None = None
    tty: bool = False
    interactive: bool = False
    timeout: float = 10.0  # seconds; kills command if still running


class BuildRequest(BaseModel):
    context: str = "."
    file: str | None = None
    tag: str
    target: str | None = None
    arch: str | None = None
    no_cache: bool = False


class TopologyValidateRequest(BaseModel):
    content: str


class CopyRequest(BaseModel):
    source: str
    destination: str
    direction: Literal["from", "to"]


class ExportRequest(BaseModel):
    output_path: str | None = None


class PruneRequest(BaseModel):
    confirm: bool = False


class NetworkCreateRequest(BaseModel):
    name: str
    subnet: str | None = None
    subnet_v6: str | None = None
    internal: bool = False
    plugin: str | None = None
    options: dict[str, str] = {}


class DetonateRequest(BaseModel):
    """Request body for ``POST /detonate``.

    ``sample`` is a host-local path to the artifact to detonate (see the
    endpoint docstring for the trust-domain caveat).
    """

    sample: str
    image: str = "alpine:latest"
    profile: str = "detonation"
    name: str | None = None
    command: list[str] | None = None
    observe_seconds: float = 30.0
    stats_interval: float = 2.0


# ==========================================================================
# Application
# ==========================================================================

_state: dict[str, Any] = {"initialised": False}


def _get_state() -> dict[str, Any]:
    if not _state["initialised"]:
        raise HTTPException(status_code=503, detail="DAEDALUS not yet initialised")
    return _state


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    ensure_daemon()
    caps = probe()
    backend = CliBackend(caps)
    audit = AuditLog()
    store = Store()
    policy = PolicyEngine(load_policy_config())

    def _policy_audit(operation: str, actor: str, result: PolicyResult) -> None:
        audit.record(
            "policy", actor=actor, actor_kind=ActorKind.SERVICE,
            args={
                "operation": operation,
                "decision": result.decision.value,
                "reason": result.reason,
            },
        )

    policy.config.on_decision = _policy_audit

    _state.update({
        "caps": caps,
        "backend": backend,
        "forge": Forge(backend, caps, policy=policy, audit=audit, store=store),
        "icarus": Icarus(backend, audit=audit, store=store, runtime_binary=caps.container_binary),
        "mint": Mint(backend, audit=audit),
        "talos": Talos(backend, caps, policy=policy, audit=audit),
        "machina": Machina(backend, caps, policy=policy, audit=audit),
        "profiles": ProfileRegistry(),
        "audit": audit,
        "store": store,
        "initialised": True,
    })
    yield


app = FastAPI(
    title="DAEDALUS API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)


# ==========================================================================
# Error handling
# ==========================================================================


@app.exception_handler(DaedalusError)
async def daedalus_error_handler(request: Any, exc: DaedalusError) -> JSONResponse:
    return JSONResponse(status_code=400, content=exc.to_dict())


# ==========================================================================
# Health
# ==========================================================================


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/health")
async def health() -> dict[str, Any]:
    s = _get_state()
    caps: Any = s["caps"]
    result: dict[str, Any] = caps.as_dict()
    return result


# ==========================================================================
# Containers
# ==========================================================================


@app.get("/containers")
async def list_containers(all: bool = Query(False)) -> list[dict[str, Any]]:
    s = _get_state()
    forge: Any = s["forge"]
    labs = await forge.list(all=all)
    return [_container_dict(lab) for lab in labs]


@app.post("/containers")
async def create_container(req: RunRequest) -> dict[str, Any]:
    s = _get_state()
    p = s["profiles"].get(req.profile)
    overrides: dict[str, Any] = {}
    if req.kernel is not None:
        overrides["kernel"] = req.kernel
    if req.cpus is not None:
        overrides["cpus"] = req.cpus
    if req.memory is not None:
        overrides["memory"] = req.memory
    if req.dns is not None:
        overrides["dns"] = req.dns
    if req.volumes is not None:
        overrides["volumes"] = req.volumes
    if req.mounts is not None:
        overrides["mounts"] = req.mounts
    if req.env is not None:
        overrides["env"] = req.env
    if req.workdir is not None:
        overrides["workdir"] = req.workdir
    if req.proxy is not None:
        overrides["proxy"] = req.proxy
    if req.cert_path is not None:
        overrides["cert_path"] = req.cert_path
    if req.no_proxy is not None:
        overrides["no_proxy"] = req.no_proxy
    if req.network is not None:
        overrides["network"] = req.network
    if req.publish:
        overrides["publish"] = req.publish
    if req.cap_add:
        overrides["cap_add"] = req.cap_add
    if req.cap_drop:
        overrides["cap_drop"] = req.cap_drop
    overrides["remove"] = req.remove
    kwargs = p.apply(**overrides)
    forge: Any = s["forge"]
    run_kwargs = {
        "name": req.name,
        "profile": req.profile,
        "command": req.command,
        "confirm_kernel": req.confirm_kernel,
        **kwargs,
    }
    if req.start:
        lab = await forge.run(
            req.image, detach=req.detach, **run_kwargs,
        )
        s["audit"].record("run", actor="service", actor_kind=ActorKind.SERVICE,
                          args={"image": req.image, "profile": req.profile})
    else:
        lab = await forge.create(req.image, **run_kwargs)
        s["audit"].record("create", actor="service", actor_kind=ActorKind.SERVICE,
                          args={"image": req.image, "profile": req.profile})
    return _container_dict(lab)


@app.get("/containers/{container_id}")
async def inspect_container(container_id: str) -> dict[str, Any]:
    s = _get_state()
    try:
        lab = await s["forge"].inspect(container_id)
        raw = lab.info.raw
        if not raw or not raw.get("configuration", {}).get("id"):
            raise HTTPException(status_code=404, detail=f"Container '{container_id}' not found")
        return raw
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/containers/{container_id}/start")
async def start_container(
    container_id: str,
    attach: bool = Query(False),
    interactive: bool = Query(False),
) -> dict[str, Any]:
    """Start a stopped container."""
    s = _get_state()
    try:
        lab = await s["forge"].start(
            container_id, attach=attach, interactive=interactive,
        )
        s["audit"].record("start", actor="service", actor_kind=ActorKind.SERVICE,
                          args={"container_id": container_id})
        return {"status": "started", "id": lab.id, "state": lab.state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/containers/{container_id}")
async def destroy_container(container_id: str, confirm: bool = Query(False)) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true required for destruction")
    s = _get_state()
    try:
        report = await s["forge"].destroy(container_id, confirm=True)
        s["audit"].record("destroy", actor="service", actor_kind=ActorKind.SERVICE,
                          args={"container_id": container_id})
        result: dict[str, Any] = {"status": "destroyed", "id": container_id}
        if report:
            result["report"] = report
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/containers/{container_id}/stop")
async def stop_container(
    container_id: str,
    timeout: int = Query(10),
    signal: str | None = Query(None),
) -> dict[str, Any]:
    """Stop a running container."""
    s = _get_state()
    try:
        lab = await s["forge"].stop(container_id, timeout=timeout, signal=signal)
        s["audit"].record("stop", actor="service", actor_kind=ActorKind.SERVICE,
                          args={"container_id": container_id})
        return {"status": "stopped", "id": lab.id, "state": lab.state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/containers/{container_id}/kill")
async def kill_container(
    container_id: str,
    signal: str = Query("KILL"),
) -> dict[str, Any]:
    """Kill a running container."""
    s = _get_state()
    try:
        lab = await s["forge"].kill(container_id, signal=signal)
        s["audit"].record("kill", actor="service", actor_kind=ActorKind.SERVICE,
                          args={"container_id": container_id, "signal": signal})
        return {"status": "killed", "id": lab.id, "state": lab.state, "signal": signal}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/containers/{container_id}/exec")
async def container_exec(container_id: str, req: ExecRequest) -> dict[str, Any]:
    s = _get_state()
    try:
        opts = ExecOptions(
            user=req.user,
            uid=req.uid,
            gid=req.gid,
            workdir=req.workdir,
            env=req.env,
            env_file=req.env_file,
            tty=req.tty,
            interactive=req.interactive,
            timeout=req.timeout,
        )
        result = await s["icarus"].exec(container_id, req.command, options=opts)
        return {"exit_code": result.exit_code, "stdout": result.stdout,
                "stderr": result.stderr}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/containers/{container_id}/logs")
async def container_logs(
    container_id: str,
    boot: bool = Query(False),
    tail: int | None = Query(None),
) -> dict[str, str]:
    s = _get_state()
    try:
        logs = await s["icarus"].logs(container_id, boot=boot, tail=tail)
        return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/containers/{container_id}/logs/stream")
async def container_logs_stream(
    request: Request,
    container_id: str,
    boot: bool = Query(False),
) -> StreamingResponse:
    """Stream container logs via Server-Sent Events."""
    s = _get_state()

    async def event_stream() -> AsyncIterator[str]:
        yield ": connected\n\n"
        seen: set[str] = set()
        try:
            initial = await s["icarus"].logs(container_id, boot=boot)
            for line in initial.splitlines():
                if line.strip() and line not in seen:
                    seen.add(line)
                    yield f"data: {line}\n\n"
        except Exception:
            yield "event: error\ndata: container not found\n\n"
            return
        while True:
            if await request.is_disconnected():
                break
            try:
                new_logs = await s["icarus"].logs(container_id, boot=boot, tail=30)
                for line in new_logs.splitlines():
                    if line.strip() and line not in seen:
                        seen.add(line)
                        yield f"data: {line}\n\n"
            except Exception:
                yield "event: error\ndata: log stream interrupted\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/containers/{container_id}/report")
async def container_report(container_id: str) -> dict[str, Any]:
    """Return the Minos behavioral report for a destroyed run."""
    s = _get_state()
    path = os.path.join(s["store"].root, f"{container_id}.report.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="report not found")
    with open(path) as f:
        return json.load(f)


# ==========================================================================
# Machines (container 1.2.2 persistent VMs)
# ==========================================================================


@app.get("/machines")
async def machine_list() -> list[dict[str, Any]]:
    """List persistent container machines."""
    s = _get_state()
    machines = await s["machina"].list()
    return [_machine_dict(m) for m in machines]


@app.post("/machines")
async def machine_create(req: MachineCreateRequest) -> dict[str, Any]:
    """Create a persistent machine (advisor warning echoed when flagged)."""
    s = _get_state()
    spec = MachineCreateSpec(
        image=req.image,
        name=req.name,
        cpus=req.cpus,
        memory=req.memory,
        home_mount=req.home_mount,
        virtualization=req.virtualization,
        kernel=req.kernel,
        set_default=req.set_default,
        no_boot=req.no_boot,
    )
    info = await s["machina"].create(spec)
    return _machine_dict(info)


@app.get("/machines/{machine_id}")
async def machine_inspect(machine_id: str) -> dict[str, Any]:
    """Inspect a single machine."""
    s = _get_state()
    info = await s["machina"].inspect(machine_id)
    return _machine_dict(info)


@app.post("/machines/{machine_id}/set")
async def machine_set(machine_id: str, req: MachineSetRequest) -> dict[str, Any]:
    """Update machine settings (changes apply after restart)."""
    s = _get_state()
    settings = MachineSetSettings(
        cpus=req.cpus,
        memory=req.memory,
        home_mount=req.home_mount,
        virtualization=req.virtualization,
        kernel=req.kernel,
    )
    await s["machina"].set(machine_id, settings)
    return {"ok": True}


@app.post("/machines/{machine_id}/set-default")
async def machine_set_default(machine_id: str) -> dict[str, Any]:
    """Make a machine the default (used when no id is given)."""
    s = _get_state()
    await s["machina"].set_default(machine_id)
    return {"ok": True}


@app.post("/machines/{machine_id}/stop")
async def machine_stop(machine_id: str) -> dict[str, Any]:
    """Stop a machine."""
    s = _get_state()
    await s["machina"].stop(machine_id)
    return {"ok": True}


@app.delete("/machines/{machine_id}")
async def machine_delete(
    machine_id: str,
    confirm: bool = Query(False),
) -> dict[str, Any]:
    """Delete a machine (destructive — confirm required)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true required for deletion")
    s = _get_state()
    await s["machina"].delete(machine_id, confirm=True)
    return {"ok": True}


@app.post("/machines/{machine_id}/run")
async def machine_run(machine_id: str, req: MachineRunRequest) -> dict[str, Any]:
    """Boot the machine if necessary and run a command inside it."""
    s = _get_state()
    result = await s["machina"].run(machine_id, req.command)
    payload: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if result.exit_code != 0:
        explanation = await s["machina"].explain_failure(machine_id)
        if explanation is not None:
            payload["explanation"] = explanation
    return payload


@app.get("/machines/{machine_id}/logs")
async def machine_logs(machine_id: str) -> dict[str, str]:
    """Dump a machine's boot/stdout logs."""
    s = _get_state()
    logs = await s["machina"].logs(machine_id)
    return {"logs": logs}


# ==========================================================================
# Stats, copy, export, prune (container 1.2.2)
# ==========================================================================


@app.get("/stats")
async def stats_all() -> list[dict[str, Any]]:
    """One-shot resource statistics for all running containers."""
    s = _get_state()
    samples = await s["forge"].stats()
    return [sample.to_dict() for sample in samples]


@app.get("/containers/{container_id}/stats")
async def container_stats(container_id: str) -> list[dict[str, Any]]:
    """One-shot resource statistics for a single container."""
    s = _get_state()
    samples = await s["forge"].stats(container_id)
    return [sample.to_dict() for sample in samples]


@app.post("/containers/{container_id}/copy")
async def container_copy(container_id: str, req: CopyRequest) -> dict[str, Any]:
    """Copy a file between a container and the host.

    ``direction`` of "from" copies out of the container
    (``source`` = container path, ``destination`` = host path); "to"
    copies into the container (``source`` = host path,
    ``destination`` = container path).
    """
    s = _get_state()
    icarus: Any = s["icarus"]
    if req.direction == "from":
        result = await icarus.copy_from(container_id, req.source, req.destination)
    else:
        result = await icarus.copy_to(container_id, req.source, req.destination)
    return {"path": result}


@app.post("/containers/{container_id}/export")
async def container_export(container_id: str, req: ExportRequest) -> dict[str, Any]:
    """Export a container's full filesystem to a tar archive."""
    s = _get_state()
    return await s["icarus"].export_fs(container_id, output_path=req.output_path)


@app.post("/containers/prune")
async def containers_prune(req: PruneRequest) -> dict[str, Any]:
    """Prune stopped containers (destructive — confirm required)."""
    if not req.confirm:
        raise HTTPException(status_code=400, detail="confirm=true required for prune")
    s = _get_state()
    result = await s["backend"].prune()
    return asdict(result)


# ==========================================================================
# Networks (container 1.2.2)
# ==========================================================================


@app.get("/networks")
async def network_list() -> list[dict[str, Any]]:
    """List all networks."""
    s = _get_state()
    networks = await s["talos"].network_list()
    return [_network_dict(n) for n in networks]


@app.post("/networks")
async def network_create(req: NetworkCreateRequest) -> dict[str, Any]:
    """Create a network."""
    s = _get_state()
    spec = NetworkSpec(
        name=req.name,
        subnet=req.subnet,
        subnet_v6=req.subnet_v6,
        internal=req.internal,
        plugin=req.plugin or "container-network-vmnet",
        options=req.options,
    )
    info = await s["talos"].network_create(spec)
    return _network_dict(info)


@app.get("/networks/{name}")
async def network_inspect(name: str) -> dict[str, Any]:
    """Inspect a single network."""
    s = _get_state()
    info = await s["talos"].network_inspect(name)
    return _network_dict(info)


@app.delete("/networks/{name}")
async def network_delete(name: str, confirm: bool = Query(False)) -> dict[str, Any]:
    """Delete a network (destructive — confirm required)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true required for deletion")
    s = _get_state()
    await s["talos"].network_delete(name, confirm=True)
    return {"status": "deleted", "name": name}


# ==========================================================================
# Images
# ==========================================================================


@app.get("/images")
async def image_list() -> list[dict[str, Any]]:
    s = _get_state()
    images = await s["mint"].list()
    return [{"name": i.name, "tag": i.tag, "size": i.size, "digest": i.digest,
             "id": i.id} for i in images]


@app.get("/images/{name:path}")
async def image_inspect(name: str) -> dict[str, Any]:
    """Inspect an image by name."""
    s = _get_state()
    try:
        img = await s["mint"].inspect(name)
        return {"name": img.name, "id": img.id, "digest": img.digest,
                "size": img.size, "raw": img.raw}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.delete("/images/{name:path}")
async def image_delete(name: str, all: bool = Query(False)) -> dict[str, Any]:
    """Delete an image."""
    s = _get_state()
    try:
        await s["mint"].delete(name, all=all)
        return {"status": "deleted", "name": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/images/pull")
async def image_pull(
    image: str = Query(...),
    platform: str | None = Query(None),
    scheme: str | None = Query(None),
) -> dict[str, Any]:
    s = _get_state()
    try:
        img = await s["mint"].pull(image, platform=platform, scheme=scheme)
        return {"status": "pulled", "name": img.name, "id": img.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/images/push")
async def image_push(
    image: str = Query(...),
    platform: str | None = Query(None),
    scheme: str | None = Query(None),
) -> dict[str, Any]:
    s = _get_state()
    try:
        await s["mint"].push(image, platform=platform, scheme=scheme)
        s["audit"].record("image_push", actor="service", actor_kind=ActorKind.SERVICE,
                          args={"image": image})
        return {"status": "pushed", "name": image}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/images/build")
async def image_build(req: BuildRequest) -> dict[str, Any]:
    s = _get_state()
    try:
        spec = BuildSpec(
            context=req.context, file=req.file, tag=req.tag,
            target=req.target, arch=req.arch, no_cache=req.no_cache,
        )
        img = await s["mint"].build(spec)
        return {"status": "built", "name": img.name, "id": img.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/images/load")
async def image_load(path: str = Query(...)) -> dict[str, Any]:
    """Load an image from an OCI-compatible tar archive."""
    s = _get_state()
    try:
        img = await s["mint"].load(path)
        return {"status": "loaded", "name": img.name, "id": img.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/images/save")
async def image_save(
    image: str = Query(...),
    output: str = Query(...),
) -> dict[str, Any]:
    """Save an image as an OCI-compatible tar archive."""
    s = _get_state()
    try:
        path = await s["mint"].save(image, output)
        return {"status": "saved", "image": image, "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/images/tag")
async def image_tag(req: ImageTagRequest) -> dict[str, Any]:
    """Tag an image (alias)."""
    s = _get_state()
    try:
        await s["mint"].tag(req.source, req.target)
        return {"status": "tagged", "source": req.source, "target": req.target}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/images/prune")
async def image_prune() -> dict[str, Any]:
    """Remove dangling/unreferenced images."""
    s = _get_state()
    try:
        removed = await s["mint"].prune()
        return {"status": "pruned", "removed": removed, "count": len(removed)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ==========================================================================
# Registry
# ==========================================================================


@app.post("/registry/login")
async def registry_login(req: RegistryLoginRequest) -> dict[str, Any]:
    s = _get_state()
    try:
        await s["backend"].registry_login(
            req.server,
            username=req.username,
            password=req.password,
            scheme=req.scheme,
        )
        s["audit"].record("registry_login", actor="service",
                          actor_kind=ActorKind.SERVICE, args={"server": req.server})
        return {"status": "logged_in", "server": req.server}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/registry/logout")
async def registry_logout(server: str = Query(...)) -> dict[str, Any]:
    s = _get_state()
    try:
        await s["backend"].registry_logout(server)
        s["audit"].record("registry_logout", actor="service",
                          actor_kind=ActorKind.SERVICE, args={"server": server})
        return {"status": "logged_out", "server": server}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/registry/default")
async def registry_default_inspect() -> dict[str, Any]:
    """Return the configured default registry host."""
    s = _get_state()
    try:
        host = await s["backend"].registry_default_inspect()
        return {"host": host}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/registry/default")
async def registry_default_set(req: RegistryDefaultRequest) -> dict[str, Any]:
    """Set the default registry host."""
    s = _get_state()
    try:
        await s["backend"].registry_default_set(req.host, scheme=req.scheme)
        s["audit"].record(
            "registry_default_set", actor="service",
            actor_kind=ActorKind.SERVICE, args={"host": req.host, "scheme": req.scheme},
        )
        return {"status": "set", "host": req.host, "scheme": req.scheme}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/registry/default")
async def registry_default_unset() -> dict[str, Any]:
    """Clear the default registry host."""
    s = _get_state()
    try:
        await s["backend"].registry_default_unset()
        s["audit"].record(
            "registry_default_unset", actor="service", actor_kind=ActorKind.SERVICE,
        )
        return {"status": "unset"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ==========================================================================
# Builder
# ==========================================================================


@app.get("/builder/status")
async def builder_status() -> dict[str, Any]:
    s = _get_state()
    try:
        return await s["backend"].builder_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/builder/start")
async def builder_start(
    cpus: int = Query(2),
    memory: str = Query("2048M"),
) -> dict[str, Any]:
    s = _get_state()
    try:
        await s["backend"].builder_start(cpus=cpus, memory=memory)
        return {"status": "started", "cpus": cpus, "memory": memory}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/builder/stop")
async def builder_stop() -> dict[str, Any]:
    s = _get_state()
    try:
        await s["backend"].builder_stop()
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/builder")
async def builder_delete(force: bool = Query(False)) -> dict[str, Any]:
    """Delete the image builder VM."""
    s = _get_state()
    try:
        await s["backend"].builder_delete(force=force)
        s["audit"].record(
            "builder_delete", actor="service",
            actor_kind=ActorKind.SERVICE, args={"force": force},
        )
        return {"status": "deleted", "force": force}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ==========================================================================
# Profiles
# ==========================================================================


@app.get("/profiles")
async def profile_list() -> list[dict[str, Any]]:
    s = _get_state()
    return [{"name": p.name, "description": p.description,
             "kernel": p.kernel, "no_dns": p.no_dns,
             "dns": p.dns, "dns_domain": p.dns_domain,
             "tmpfs": p.tmpfs, "cpus": p.cpus, "memory": p.memory}
            for p in s["profiles"].list()]


@app.post("/topology/validate")
async def topology_validate(req: TopologyValidateRequest) -> dict[str, Any]:
    """Validate a Talos topology YAML template (parse-only; networks not created)."""
    try:
        data = yaml.safe_load(req.content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"invalid YAML: {e}") from e
    if not isinstance(data, dict) or "name" not in data:
        raise HTTPException(status_code=400, detail="topology must include a name")
    try:
        tpl = Talos.parse_topology(data)
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "valid": True,
        "name": tpl.name,
        "description": tpl.description,
        "networks": len(tpl.networks),
        "attachments": len(tpl.attachments),
        "dns_entries": len(tpl.dns_entries),
        "internal": tpl.internal,
    }


# ==========================================================================
# Detonation
# ==========================================================================


@app.post("/detonate")
async def detonate(req: DetonateRequest) -> dict[str, Any]:
    """Detonate a host-local sample in a disposable, isolated container.

    Blocking call (roughly 30-90 s) running the full detonation loop:
    policy gate, disposable internal network, sample delivery, observation
    (stats sampled every ``stats_interval``), judge (Minos behavioral
    report + risk score), persist (report artifact + store entry), destroy.

    ``sample`` must be a path on the daemon host — the API shares the
    daemon's trust domain, so the file is copied from the host filesystem
    into the container.  Remote/URL samples are not accepted.

    Returns the detonate result dict: ``run_id``, ``report``,
    ``report_path``, ``artifacts``, ``risk_score``, ``exit_code``.  Failures
    mid-loop raise ``DetonationError`` (400) with ``partial_evidence``; the
    VM and disposable network are destroyed first (best-effort).
    """
    s = _get_state()
    p = s["profiles"].get(req.profile)
    kwargs = p.apply()
    return await s["forge"].detonate(
        req.sample,
        image=req.image,
        profile=req.profile,
        name=req.name,
        command=req.command,
        observe_seconds=req.observe_seconds,
        stats_interval=req.stats_interval,
        profile_kwargs=kwargs,
    )


# ==========================================================================
# Experiments (store manifests)
# ==========================================================================


@app.get("/experiments")
async def list_experiments() -> list[dict[str, Any]]:
    s = _get_state()
    return [asdict(m) for m in s["store"].list()]


@app.get("/experiments/{run_id}")
async def get_experiment(run_id: str) -> dict[str, Any]:
    s = _get_state()
    manifest = s["store"].get(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return asdict(manifest)


@app.get("/experiments/{run_id}/report")
async def get_experiment_report(run_id: str) -> dict[str, Any]:
    """Return the detonation behavioral report for an experiment.

    Reads ``<store.root>/<run_id>.report.json``, written by
    ``forge.detonate`` alongside the store manifest.  404 when the
    experiment does not exist or the report artifact is missing.
    """
    s = _get_state()
    manifest = s["store"].get(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    path = os.path.join(s["store"].root, f"{run_id}.report.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="report not found")
    with open(path) as f:
        return json.load(f)


@app.delete("/experiments/{run_id}")
async def delete_experiment(run_id: str) -> dict[str, Any]:
    s = _get_state()
    deleted = s["store"].delete(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="experiment not found")
    return {"status": "deleted", "run_id": run_id}


@app.delete("/experiments")
async def clear_experiments() -> dict[str, Any]:
    s = _get_state()
    count = s["store"].clear()
    return {"status": "cleared", "count": count}


# ==========================================================================
# System
# ==========================================================================


@app.get("/system/status")
async def system_status() -> dict[str, Any]:
    s = _get_state()
    status = await s["forge"].system_status()
    return {
        "container_version": status.container_version,
        "container_commit": status.container_commit,
        "apiserver_running": status.apiserver_running,
        "container_count": status.container_count,
        "running_count": status.running_count,
        "disk_usage": status.disk_usage,
        "capabilities": status.capabilities,
    }


@app.get("/system/audit")
async def system_audit(
    operation: str | None = Query(None),
    actor: str | None = Query(None),
    since: str | None = Query(None),
    limit: int = Query(100),
) -> list[dict[str, Any]]:
    """Query the audit log."""
    s = _get_state()
    audit: Any = s["audit"]
    entries = audit.query(operation=operation, actor=actor, since=since, limit=limit)
    return [{
        "operation": e.operation,
        "actor": e.actor,
        "actor_kind": e.actor_kind.value,
        "args": e.args,
        "result": e.result,
        "error": e.error,
        "timestamp": e.timestamp,
        "entry_id": e.entry_id,
    } for e in entries]


@app.get("/system/dns")
async def system_dns_list() -> list[str]:
    s = _get_state()
    try:
        return await s["talos"].system_dns_list()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/system/dns")
async def system_dns_create(domain: str = Query(...)) -> dict[str, Any]:
    s = _get_state()
    try:
        await s["talos"].system_dns_create(domain)
        s["audit"].record("system_dns_create", actor="service",
                          actor_kind=ActorKind.SERVICE, args={"domain": domain})
        return {"status": "created", "domain": domain}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/system/dns/{domain}")
async def system_dns_delete(domain: str) -> dict[str, Any]:
    s = _get_state()
    try:
        await s["talos"].system_dns_delete(domain)
        s["audit"].record("system_dns_delete", actor="service",
                          actor_kind=ActorKind.SERVICE, args={"domain": domain})
        return {"status": "deleted", "domain": domain}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/system/start")
async def system_start() -> dict[str, Any]:
    """Start container system services (apiserver)."""
    s = _get_state()
    try:
        await s["backend"].system_start()
        s["audit"].record("system_start", actor="service", actor_kind=ActorKind.SERVICE)
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/system/stop")
async def system_stop() -> dict[str, Any]:
    """Stop all container system services."""
    s = _get_state()
    try:
        await s["backend"].system_stop()
        s["audit"].record("system_stop", actor="service", actor_kind=ActorKind.SERVICE)
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/system/logs")
async def system_logs(last: str = Query("5m")) -> dict[str, str]:
    """Fetch container system logs."""
    s = _get_state()
    try:
        logs = await s["backend"].system_logs(last=last, follow=False)
        return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/system/logs/stream")
async def system_logs_stream(last: str = Query("5m")) -> StreamingResponse:
    """Poll container system logs."""
    s = _get_state()

    async def log_stream() -> AsyncIterator[str]:
        while True:
            chunk = await s["backend"].system_logs(last=last, follow=False)
            yield chunk
            await asyncio.sleep(2)

    return StreamingResponse(log_stream(), media_type="text/plain")


@app.post("/system/kernel/set")
async def system_kernel_set(req: SystemKernelSetRequest) -> dict[str, Any]:
    """Set the default container kernel."""
    s = _get_state()
    try:
        await s["backend"].system_kernel_set(
            binary=req.binary,
            tar=req.tar,
            arch=req.arch,
            recommended=req.recommended,
        )
        s["audit"].record(
            "system_kernel_set", actor="service", actor_kind=ActorKind.SERVICE,
            args=req.model_dump(),
        )
        return {"status": "set", **req.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/system/restart")
async def system_restart() -> dict[str, Any]:
    """Restart the container apiserver."""
    s = _get_state()
    try:
        await s["backend"].system_restart()
        s["audit"].record("system_restart", actor="service", actor_kind=ActorKind.SERVICE)
        return {"status": "restarted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/system/events")
async def system_events_stream(request: Request) -> StreamingResponse:
    """SSE stream of container lifecycle events."""
    s = _get_state()

    async def event_stream() -> AsyncIterator[str]:
        prev: tuple[int, int] | None = None
        while True:
            if await request.is_disconnected():
                break
            try:
                forge: Forge = s["forge"]
                labs = await forge.list(all=True)
                count = len(labs)
                running = sum(1 for lab in labs if lab.state == "running")
                snapshot = (count, running)
                if snapshot != prev:
                    yield (
                        f"event: status\ndata: "
                        f"{json.dumps({'total': count, 'running': running})}\n\n"
                    )
                    prev = snapshot
            except Exception:
                pass
            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ==========================================================================
# Static UI
# ==========================================================================

_ui_dir = os.path.join(os.path.dirname(__file__), "..", "..", "ui", "dist")
if os.path.isdir(_ui_dir):
    app.mount("/ui", StaticFiles(directory=_ui_dir, html=True), name="ui")


# ==========================================================================
# Main
# ==========================================================================


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8420)


if __name__ == "__main__":
    main()
