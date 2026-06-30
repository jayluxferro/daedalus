"""Network helpers for Apple container inspect JSON."""

from __future__ import annotations

from typing import Any


def network_entries(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Return runtime network entries with addresses (inspect JSON)."""
    nets = raw.get("networks", [])
    if not isinstance(nets, list):
        return []
    return [n for n in nets if isinstance(n, dict)]


def has_network_addresses(raw: dict[str, Any]) -> bool:
    return any(n.get("address") for n in network_entries(raw))


def primary_ip(raw: dict[str, Any]) -> str | None:
    for net in network_entries(raw):
        addr = net.get("address", "")
        if isinstance(addr, str) and addr:
            return addr.split("/", 1)[0]
    return None


def network_names(raw: dict[str, Any]) -> list[str]:
    """Configured network names (available from list JSON)."""
    cfg = raw.get("configuration", raw)
    if not isinstance(cfg, dict):
        return []
    nets = cfg.get("networks", [])
    if isinstance(nets, list):
        return [str(n) for n in nets]
    return []
