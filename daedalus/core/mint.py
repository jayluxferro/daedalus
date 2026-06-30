"""M3 — mint: image build/registry plane.

Mint owns the full image lifecycle for what container v0.1.0 supports:

* Build (Containerfile/Dockerfile, multi-arch, build-args, labels)
* Pull / push / save / load / tag / delete / inspect / list / prune
* Registry login / logout
* Builder start / stop / status / delete

OCI-compatible both ways — interops with Docker ``save``/``load``.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from typing import Any

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import Backend, BuildSpec


@dataclass
class ImageInfo:
    """Enriched image metadata with provenance."""

    id: str
    name: str = ""
    tag: str = "latest"
    digest: str = ""
    size: int = 0
    created_at: str = ""
    provenance: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class Mint:
    """Image plane — build, registry, and image lifecycle.

    Parameters
    ----------
    backend:
        The active ``Backend`` implementation.
    audit:
        Optional audit log for operation recording.
    """

    def __init__(self, backend: Backend, *, audit: AuditLog | None = None) -> None:
        self._backend = backend
        self._audit = audit or AuditLog()
        self._inventory: dict[str, ImageInfo] = {}

    # ==================================================================
    # Build
    # ==================================================================

    async def build(self, spec: BuildSpec) -> ImageInfo:
        """Build an image from a Containerfile/Dockerfile."""
        tag = await self._backend.build(spec)
        img = ImageInfo(
            id=tag, name=tag, tag="latest",
            provenance={"source": "build", "context": spec.context},
        )
        self._inventory[img.id] = img
        self._audit.record(
            "build", actor="mint", actor_kind=ActorKind.SERVICE,
            args={"tag": spec.tag, "context": spec.context},
        )
        return img

    # ==================================================================
    # Pull / Push
    # ==================================================================

    async def pull(self, image: str, platform: str | None = None) -> ImageInfo:
        """Pull an image from a registry."""
        await self._backend.image_pull(image, platform=platform)
        info = await self._backend.image_inspect(image)
        # container v0.1.0 OCI index format: {"name": "...", "index": {"digest": "...", "size": ...}}
        img_name = info.get("name", image)
        img_id = info.get("name", "")
        digest = ""
        if isinstance(info.get("index"), dict):
            digest = info["index"].get("digest", "")
        img = ImageInfo(
            id=img_id,
            name=img_name,
            digest=digest,
            raw=info,
            provenance={"source": "pull", "image": image},
        )
        if img.id:
            self._inventory[img.id] = img
        self._audit.record(
            "pull", actor="mint", actor_kind=ActorKind.SERVICE,
            args={"image": image},
        )
        return img

    async def push(self, image: str) -> None:
        """Push an image to a registry."""
        await self._backend.image_push(image)
        self._audit.record(
            "push", actor="mint", actor_kind=ActorKind.SERVICE,
            args={"image": image},
        )

    # ==================================================================
    # Save / Load (Docker interop)
    # ==================================================================

    async def save(self, image: str, output: str) -> str:
        """Save an image as an OCI-compatible tar archive."""
        path = await self._backend.image_save(image, output)
        self._audit.record(
            "save", actor="mint", actor_kind=ActorKind.SERVICE,
            args={"image": image, "output": output},
        )
        return path

    async def load(self, input_path: str) -> ImageInfo:
        """Load an image from an OCI-compatible tar archive."""
        name = await self._backend.image_load(input_path)
        info = await self._backend.image_inspect(name)
        img = ImageInfo(
            id=info.get("id", ""),
            name=name,
            raw=info,
            provenance={"source": "load", "path": input_path},
        )
        if img.id:
            self._inventory[img.id] = img
        self._audit.record(
            "load", actor="mint", actor_kind=ActorKind.SERVICE,
            args={"input_path": input_path},
        )
        return img

    # ==================================================================
    # Tag / Delete / Inspect / List / Prune
    # ==================================================================

    async def tag(self, source: str, target: str) -> None:
        await self._backend.image_tag(source, target)
        self._audit.record(
            "image_tag", actor="mint", actor_kind=ActorKind.SERVICE,
            args={"source": source, "target": target},
        )

    async def delete(self, image: str, force: bool = False) -> None:
        await self._backend.image_delete(image, force=force)
        to_remove = [k for k, v in self._inventory.items() if v.name == image]
        for k in to_remove:
            del self._inventory[k]
        self._audit.record(
            "image_delete", actor="mint", actor_kind=ActorKind.SERVICE,
            args={"image": image, "force": force},
        )

    async def inspect(self, image: str) -> ImageInfo:
        data = await self._backend.image_inspect(image)
        # container v0.1.0 OCI index format: {"name": "...", "index": {"digest": "...", "size": ...}}
        img_name = data.get("name", image)
        img_index = data.get("index", {}) if isinstance(data.get("index"), dict) else {}
        return ImageInfo(
            id=img_name,
            name=img_name,
            digest=img_index.get("digest", ""),
            size=img_index.get("size", 0),
            raw=data,
        )

    async def list(self, quiet: bool = False) -> builtins.list[ImageInfo]:
        raw_list = await self._backend.image_list(quiet=quiet)
        result = []
        for raw in raw_list:
            # container v0.1.0 format: {"reference": "docker.io/library/alpine:latest",
            #  "descriptor": {"digest": "sha256:...", "mediaType": "...", "size": ...}}
            reference = raw.get("reference", "")
            descriptor = raw.get("descriptor", {}) if isinstance(raw.get("descriptor"), dict) else {}
            # Parse "docker.io/library/alpine:latest" into name + tag
            if ":" in reference:
                name_part, tag_part = reference.rsplit(":", 1)
            else:
                name_part, tag_part = reference, "latest"
            img = ImageInfo(
                id=descriptor.get("digest", reference),
                name=name_part,
                tag=tag_part,
                digest=descriptor.get("digest", ""),
                size=descriptor.get("size", 0),
                created_at="",
                raw=raw,
            )
            result.append(img)
            if img.id:
                self._inventory[img.id] = img
        return result

    async def prune(self) -> builtins.list[str]:
        removed = await self._backend.image_prune()
        self._audit.record(
            "image_prune", actor="mint", actor_kind=ActorKind.SERVICE,
            args={},
        )
        return removed

    def inventory(self) -> dict[str, ImageInfo]:
        return dict(self._inventory)
