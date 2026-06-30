"""Unit tests for icarus log aggregation."""

from __future__ import annotations

import pytest

from daedalus.core.backend import Backend, BuildSpec, ContainerInfo, ContainerState, ExecResult, RunSpec
from daedalus.core.icarus import Icarus


class LogBackend(Backend):
    """Minimal backend stub for log tests."""

    def __init__(self) -> None:
        self.containers = [
            ContainerInfo(
                id="c1", name="one", image="alpine:latest",
                state=ContainerState.RUNNING, raw={},
            ),
            ContainerInfo(
                id="c2", name="two", image="alpine:latest",
                state=ContainerState.STOPPED, raw={},
            ),
        ]
        self.log_calls: list[tuple[str, dict]] = []

    async def create(self, spec: RunSpec) -> ContainerInfo:
        raise NotImplementedError

    async def run(self, spec: RunSpec) -> ContainerInfo:
        raise NotImplementedError

    async def start(self, container_id: str, **kwargs: object) -> None:
        pass

    async def stop(self, container_id: str, timeout: int = 10, **kwargs: object) -> None:
        pass

    async def kill(self, container_id: str, signal: str = "KILL") -> None:
        pass

    async def delete(self, container_id: str, force: bool = False) -> None:
        pass

    async def list(self, all: bool = False) -> list[ContainerInfo]:
        if all:
            return self.containers
        return [c for c in self.containers if c.state == ContainerState.RUNNING]

    async def inspect(self, container_id: str) -> ContainerInfo:
        raise NotImplementedError

    async def logs(
        self,
        container_id: str,
        *,
        follow: bool = False,
        follow_seconds: float | None = None,
        boot: bool = False,
        tail: int | None = None,
    ) -> str:
        self.log_calls.append((container_id, {
            "follow": follow, "follow_seconds": follow_seconds,
            "boot": boot, "tail": tail,
        }))
        return f"log:{container_id}\n"

    async def exec(self, container_id: str, argv: list[str], **opts: object) -> ExecResult:
        raise NotImplementedError

    async def image_pull(self, image: str, **kwargs: object) -> None:
        pass

    async def image_push(self, image: str, **kwargs: object) -> None:
        pass

    async def image_save(self, image: str, output: str) -> str:
        return output

    async def image_load(self, input_path: str) -> str:
        return "img:latest"

    async def image_tag(self, source: str, target: str) -> None:
        pass

    async def image_delete(self, image: str, *, all: bool = False) -> None:
        pass

    async def image_inspect(self, image: str) -> dict:
        return {}

    async def image_list(self, quiet: bool = False) -> list[dict]:
        return []

    async def image_prune(self) -> list[str]:
        return []

    async def build(self, spec: BuildSpec) -> str:
        return "built:latest"

    async def registry_login(self, server: str, **kwargs: object) -> None:
        pass

    async def registry_logout(self, server: str) -> None:
        pass

    async def registry_default_inspect(self) -> str:
        return ""

    async def registry_default_set(self, host: str, scheme: str | None = None) -> None:
        pass

    async def registry_default_unset(self) -> None:
        pass

    async def builder_status(self) -> dict:
        return {}

    async def builder_start(self, cpus: int = 2, memory: str = "2048M") -> None:
        pass

    async def builder_stop(self) -> None:
        pass

    async def builder_delete(self, force: bool = False) -> None:
        pass

    async def system_start(self) -> None:
        pass

    async def system_stop(self) -> None:
        pass

    async def system_restart(self) -> None:
        pass

    async def system_logs(
        self, last: str = "5m", follow: bool = False, follow_seconds: float | None = None,
    ) -> str:
        return f"system:{last}:follow={follow}"

    async def system_kernel_set(self, **kwargs: object) -> None:
        pass

    async def system_dns_create(self, domain: str) -> None:
        pass

    async def system_dns_delete(self, domain: str) -> None:
        pass

    async def system_dns_list(self) -> list[str]:
        return []

    async def system_df(self) -> dict:
        return {}

    async def _run_cli(self, *args: str, **kwargs: object) -> tuple[int, str, str]:
        return (0, "", "")


@pytest.mark.asyncio
async def test_logs_all_aggregates_containers_and_system() -> None:
    backend = LogBackend()
    icarus = Icarus(backend)
    result = await icarus.logs_all(
        all_containers=True,
        include_system=True,
        system_last="1h",
        follow=False,
    )
    assert result["count"] == 2
    assert len(result["containers"]) == 2
    assert result["containers"][0]["logs"] == "log:c1\n"
    assert result["system"] == "system:1h:follow=False"
    assert {c[0] for c in backend.log_calls} == {"c1", "c2"}
