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

    # Images
    GET  /images
    POST /images/pull
    GET  /images/{name}
    DELETE /images/{name}

    # Profiles
    GET  /profiles

    # System
    GET  /system/status
    GET  /system/audit
    GET  /system/events               (SSE)

    # UI (static SPA)
    GET  /ui/*
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import yaml

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.capabilities import probe
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import DaedalusError
from daedalus.core.backend import BuildSpec
from daedalus.core.forge import Forge
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.mint import Mint
from daedalus.core.policy import PolicyEngine, PolicyResult
from daedalus.core.profiles import ProfileRegistry
from daedalus.core.store import Store
from daedalus.core.talos import Talos
from daedalus.core.network import network_names, primary_ip

# ==========================================================================
# Request models
# ==========================================================================


class RunRequest(BaseModel):
    image: str
    name: str | None = None
    profile: str = "detonation"
    detach: bool = True
    command: list[str] | None = None
    kernel: str | None = None
    cpus: int | None = None
    memory: str | None = None
    dns: list[str] | None = None
    volumes: list[str] | None = None
    mounts: list[str] | None = None
    confirm_kernel: bool = False


def _container_dict(lab: Any) -> dict[str, Any]:
    raw = lab.info.raw
    return {
        "id": lab.id,
        "name": lab.name,
        "image": lab.image,
        "state": lab.state,
        "profile": lab.profile,
        "ip": primary_ip(raw),
        "networks": network_names(raw),
    }


class ExecRequest(BaseModel):
    command: list[str]
    user: str | None = None
    workdir: str | None = None
    env: dict[str, str] | None = None


class BuildRequest(BaseModel):
    context: str = "."
    file: str | None = None
    tag: str
    target: str | None = None
    arch: str | None = None
    no_cache: bool = False


class TopologyValidateRequest(BaseModel):
    content: str


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
    caps = probe()
    backend = CliBackend(caps)
    audit = AuditLog()
    store = Store()
    policy = PolicyEngine()

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
        "icarus": Icarus(backend, audit=audit, runtime_binary=caps.container_binary),
        "mint": Mint(backend, audit=audit),
        "talos": Talos(backend, caps, audit=audit),
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
    kwargs = p.apply(**overrides)
    forge: Any = s["forge"]
    lab = await forge.run(
        req.image, name=req.name, detach=req.detach,
        profile=req.profile, command=req.command,
        confirm_kernel=req.confirm_kernel, **kwargs,
    )
    s["audit"].record("run", actor="service", actor_kind=ActorKind.SERVICE,
                      args={"image": req.image, "profile": req.profile})
    return _container_dict(lab)


@app.get("/containers/{container_id}")
async def inspect_container(container_id: str) -> dict[str, Any]:
    s = _get_state()
    try:
        lab = await s["forge"].inspect(container_id)
        return lab.info.raw
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/containers/{container_id}/start")
async def start_container(container_id: str) -> dict[str, Any]:
    """Start a stopped container."""
    s = _get_state()
    try:
        lab = await s["forge"].start(container_id)
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
async def stop_container(container_id: str) -> dict[str, Any]:
    """Stop a running container."""
    s = _get_state()
    try:
        lab = await s["forge"].stop(container_id)
        s["audit"].record("stop", actor="service", actor_kind=ActorKind.SERVICE,
                          args={"container_id": container_id})
        return {"status": "stopped", "id": lab.id, "state": lab.state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/containers/{container_id}/exec")
async def container_exec(container_id: str, req: ExecRequest) -> dict[str, Any]:
    s = _get_state()
    try:
        opts = ExecOptions(user=req.user, workdir=req.workdir, env=req.env)
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
    container_id: str,
    boot: bool = Query(False),
) -> StreamingResponse:
    """Stream container logs via Server-Sent Events."""
    s = _get_state()

    async def event_stream() -> AsyncIterator[str]:
        seen: set[str] = set()
        try:
            initial = await s["icarus"].logs(container_id, boot=boot)
            for line in initial.splitlines():
                if line.strip() and line not in seen:
                    seen.add(line)
                    yield f"data: {line}\n\n"
        except Exception:
            yield f"event: error\ndata: container not found\n\n"
            return
        while True:
            try:
                new_logs = await s["icarus"].logs(container_id, boot=boot, tail=30)
                for line in new_logs.splitlines():
                    if line.strip() and line not in seen:
                        seen.add(line)
                        yield f"data: {line}\n\n"
            except Exception:
                yield f"event: error\ndata: log stream interrupted\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.websocket("/containers/{container_id}/exec")
async def container_exec_ws(websocket: WebSocket, container_id: str) -> None:
    """Interactive terminal via Icarus PTY shell (audited)."""
    await websocket.accept()
    s = _get_state()
    icarus: Icarus = s["icarus"]
    session = None
    master_fd: int | None = None
    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _on_pty_readable() -> None:
        if master_fd is None:
            return
        try:
            data = os.read(master_fd, 4096)
            out_q.put_nowait(data if data else None)
        except OSError:
            out_q.put_nowait(None)

    try:
        session = await icarus.spawn_shell(
            container_id, actor="service", actor_kind=ActorKind.SERVICE,
        )
        master_fd = session.master_fd
        loop.add_reader(master_fd, _on_pty_readable)

        async def pump_pty_to_ws() -> None:
            while True:
                chunk = await out_q.get()
                if not chunk:
                    break
                await websocket.send_bytes(chunk)

        async def pump_ws_to_pty() -> None:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") and master_fd is not None:
                    os.write(master_fd, msg["bytes"])
                elif text := msg.get("text"):
                    if master_fd is not None:
                        os.write(master_fd, text.encode())

        pty_task = asyncio.create_task(pump_pty_to_ws())
        try:
            await pump_ws_to_pty()
        finally:
            pty_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pty_task
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(f"\r\n\x1b[31m{e}\x1b[0m\r\n")
        except Exception:
            pass
    finally:
        if master_fd is not None:
            loop.remove_reader(master_fd)
        if session is not None:
            await icarus.close_shell(
                session, actor="service", actor_kind=ActorKind.SERVICE,
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
async def image_delete(name: str, force: bool = Query(False)) -> dict[str, Any]:
    """Delete an image."""
    s = _get_state()
    try:
        await s["mint"].delete(name, force=force)
        return {"status": "deleted", "name": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/images/pull")
async def image_pull(image: str = Query(...)) -> dict[str, Any]:
    s = _get_state()
    try:
        img = await s["mint"].pull(image)
        return {"status": "pulled", "name": img.name, "id": img.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/images/push")
async def image_push(image: str = Query(...)) -> dict[str, Any]:
    s = _get_state()
    try:
        await s["mint"].push(image)
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


@app.get("/system/events")
async def system_events_stream() -> StreamingResponse:
    """SSE stream of container lifecycle events."""
    s = _get_state()

    async def event_stream() -> AsyncIterator[str]:
        prev: tuple[int, int] | None = None
        while True:
            try:
                forge: Forge = s["forge"]
                labs = await forge.list(all=True)
                count = len(labs)
                running = sum(1 for lab in labs if lab.state == "running")
                snapshot = (count, running)
                if snapshot != prev:
                    yield f"event: status\ndata: {json.dumps({'total': count, 'running': running})}\n\n"
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
