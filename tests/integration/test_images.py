"""Integration tests — real image commands."""

from __future__ import annotations

import pytest

from daedalus.core.cli_backend import CliBackend
from daedalus.core.mint import Mint


@pytest.mark.integration
class TestRealImages:
    """End-to-end image tests using the real container CLI."""

    async def test_image_list_json(self, backend: CliBackend) -> None:
        """``container image list --format json`` returns valid JSON."""
        result = await backend.image_list()
        assert isinstance(result, list)

    async def test_pull_alpine(self, mint: Mint) -> None:
        """Pull alpine:latest and verify it appears in the list."""
        img = await mint.pull("alpine:latest")
        assert img.name

        images = await mint.list()
        names = [i.name for i in images]
        assert any("alpine" in name for name in names)

    async def test_image_inspect(self, backend: CliBackend) -> None:
        """Inspect the pulled alpine image."""
        info = await backend.image_inspect("alpine:latest")
        assert info  # should have some data

    async def test_image_tag(self, backend: CliBackend) -> None:
        """Tag an image and verify the tag appears."""
        try:
            await backend.image_tag("alpine:latest", "daedalus-test:latest")
            images = await backend.image_list()
            references = [i.get("reference", "") for i in images]
            assert any("daedalus-test" in r for r in references)
        finally:
            try:
                await backend.image_delete("daedalus-test:latest", force=True)
            except Exception:
                pass
