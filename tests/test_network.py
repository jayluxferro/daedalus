"""Tests for network helpers."""

from __future__ import annotations

from daedalus.core.network import has_network_addresses, network_names, primary_ip


def test_primary_ip_from_inspect() -> None:
    raw = {
        "networks": [
            {"network": "default", "address": "192.168.64.2/24", "gateway": "192.168.64.1"},
        ],
    }
    assert primary_ip(raw) == "192.168.64.2"
    assert has_network_addresses(raw)


def test_network_names_from_list_json() -> None:
    raw = {"configuration": {"networks": ["default", "lab"]}}
    assert network_names(raw) == ["default", "lab"]
