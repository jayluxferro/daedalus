"""M8b — HTTP/REST API for services and web UIs.

Resource-oriented REST API over the Core Engine.  Bind localhost-only by
default; auth required before exposing.

Endpoints:
    GET  /health
    GET  /containers
    POST /containers
    GET  /containers/{id}
    DELETE /containers/{id}
    POST /containers/{id}/exec
    GET  /containers/{id}/logs
    GET  /images
    POST /images/pull
    GET  /profiles
    GET  /system/status
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.capabilities import probe
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import DaedalusError
from daedalus.core.forge import Forge
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.mint import Mint
from daedalus.core.policy import PolicyEngine
from daedalus.core.profiles import ProfileRegistry
from daedalus.core.store import Store

# ==========================================================================
# Request models
# ==========================================================================


class RunRequest(BaseModel):
    image: str
    name: str | None = None
    profile: str = "detonation"
    detach: bool = True
    command: list[str] | None = None


class ExecRequest(BaseModel):
    command: list[str]
    user: str | None = None
    workdir: str | None = None
    env: dict[str, str] | None = None


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

    _state.update({
        "caps": caps,
        "backend": backend,
        "forge": Forge(backend, caps, policy=policy, audit=audit, store=store),
        "icarus": Icarus(backend, audit=audit),
        "mint": Mint(backend, audit=audit),
        "profiles": ProfileRegistry(),
        "audit": audit,
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
    result: list[dict[str, Any]] = [{"id": lab.id, "name": lab.name, "image": lab.image,
             "state": lab.state, "profile": lab.profile} for lab in labs]
    return result


@app.post("/containers")
async def create_container(req: RunRequest) -> dict[str, Any]:
    s = _get_state()
    p = s["profiles"].get(req.profile)
    kwargs = p.apply()
    forge: Any = s["forge"]
    lab = await forge.run(
        req.image, name=req.name, detach=req.detach,
        profile=req.profile, command=req.command, **kwargs,
    )
    s["audit"].record("run", actor="service", actor_kind=ActorKind.SERVICE,
                      args={"image": req.image, "profile": req.profile})
    return {"id": lab.id, "name": lab.name, "image": lab.image, "state": lab.state}


@app.get("/containers/{container_id}")
async def inspect_container(container_id: str) -> dict[str, Any]:
    s = _get_state()
    try:
        lab = await s["forge"].inspect(container_id)
        return lab.info.raw
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.delete("/containers/{container_id}")
async def destroy_container(container_id: str, confirm: bool = Query(False)) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true required for destruction")
    s = _get_state()
    try:
        await s["forge"].destroy(container_id, confirm=True)
        s["audit"].record("destroy", actor="service", actor_kind=ActorKind.SERVICE,
                          args={"container_id": container_id})
        return {"status": "destroyed", "id": container_id}
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


# ==========================================================================
# Images
# ==========================================================================


@app.get("/images")
async def image_list() -> list[dict[str, Any]]:
    s = _get_state()
    images = await s["mint"].list()
    return [{"name": i.name, "tag": i.tag, "size": i.size} for i in images]


@app.post("/images/pull")
async def image_pull(image: str = Query(...)) -> dict[str, Any]:
    s = _get_state()
    try:
        img = await s["mint"].pull(image)
        return {"status": "pulled", "name": img.name, "id": img.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ==========================================================================
# Profiles
# ==========================================================================


@app.get("/profiles")
async def profile_list() -> list[dict[str, str]]:
    s = _get_state()
    return [{"name": p.name, "description": p.description}
            for p in s["profiles"].list()]


# ==========================================================================
# System
# ==========================================================================


@app.get("/system/status")
async def system_status() -> dict[str, Any]:
    s = _get_state()
    status = await s["forge"].system_status()
    return {
        "container_version": status.container_version,
        "apiserver_running": status.apiserver_running,
        "container_count": status.container_count,
        "running_count": status.running_count,
        "disk_usage": status.disk_usage,
    }


# ==========================================================================
# Main
# ==========================================================================


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8420)


if __name__ == "__main__":
    main()
