"""Unit tests for the L1 output parsers and models (no daemon required).

The parsers are pure static methods on ``OutputParser`` and the rate
math lives on the models, so these tests run against the literal wire
shapes captured in SPEC §2 on container 1.2.2.
"""

from __future__ import annotations

from typing import Any

import pytest

from daedalus.core.backend import (
    BuildSpec,
    ClusterCreateSpec,
    MachineCreateSpec,
    MachineInfo,
    MachineSetSettings,
    NetworkInfo,
    NetworkSpec,
    PruneResult,
    RunSpec,
    StatsSample,
    VolumeInfo,
    VolumeSpec,
)
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.cli_backend import CliBackend, OutputParser
from daedalus.core.exceptions import CapabilityError

# Literal wire shapes from SPEC §2.1 / §2.5 (captured live on container 1.2.2).

STATS_JSON = """[{
  "blockReadBytes": 2596864, "blockWriteBytes": 0,
  "cpuUsageUsec": 2219, "id": "f634ad0e-abc",
  "memoryLimitBytes": 1073741824, "memoryUsageBytes": 2764800,
  "networkRxBytes": 28040, "networkTxBytes": 602,
  "numProcesses": 1
}]"""

NETWORK_LIST_JSON = """[{"configuration": {"creationDate": "2026-08-20T00:56:04Z",
   "labels": {"com.apple.container.resource.role": "builtin"},
   "mode": "nat", "name": "default", "options": {}, "plugin": "container-network-vmnet"},
  "id": "default",
  "status": {"ipv4Gateway": "192.168.64.1", "ipv4Subnet": "192.168.64.0/24",
             "ipv6Subnet": "fd8b:1877:bb8f:91bb::/64"}}]"""

# Literal wire shape from SPEC §2 (captured live on container 1.2.2).
# The source path exceeds 100 chars, so the JSON is assembled from
# adjacent string literals (the parsed value is byte-identical).
VOLUME_LIST_JSON = (
    '[{"configuration": {"creationDate": "2026-08-22T06:10:50Z", '
    '"driver": "local", "format": "ext4", "labels": {}, "name": '
    '"daedalus-probe-v", "options": {"size": "100M"}, "sizeInBytes": '
    '104857600, "source": "/Users/jay/Library/Application Support/'
    'com.apple.container/volumes/daedalus-probe-v/volume.img"}, '
    '"id": "daedalus-probe-v"}]'
)

# Literal wire shape for a k8s plugin node + a plain container
# (SPEC phase 8 §2): cluster node containers carry the plugin label
# ``com.apple.container.plugin == "k8s"`` and a ``pluginRole``
# (control-plane | worker) alongside the regular list-item fields.
# Literal wire shapes from SPEC phase 9 §2 (captured live on container 1.3.1).

# ``system property list`` emits TOML by default (the --format default).
PROPERTY_LIST_TOML = """[build]
cpus = 2
image = "ghcr.io/apple/container-builder-shim/builder:0.13.1"
memory = "2048mb"
rosetta = true

[container]
cpus = 4
memory = "1gb"

[dns]

[kernel]
binaryPath = "opt/kata/share/kata-containers/vmlinux-6.18.35-197-debug"
digest = "sha256:8736c054d9223974735394f822000823baef509e1c33405ec798240fa9b6e4b5"
url = "https://github.com/kata-containers/kata-containers/releases/download/3.32.0/kata-static-3.32.0-arm64.tar.zst"

[machine]
cpus = 8
homeMount = "rw"
memory = "24gb"
virtualization = false

[network]

[registry]
domain = "docker.io"

[vminit]
image = "ghcr.io/apple/containerization/vminit:0.42.0"
"""

# ``system version --format json`` — an array of per-service objects.
SYSTEM_VERSION_JSON = """[{"appName":"container","buildType":"release",
  "commit":"a9a62e28f6beb88940122a3d7b286f2d5ae8053a","version":"1.3.1"},
 {"appName":"container-apiserver","buildType":"release",
  "commit":"a9a62e28f6beb88940122a3d7b286f2d5ae8053a",
  "version":"container-apiserver version 1.3.1 (build: release, commit: a9a62e2)"}]"""

# ``system df --format json`` — per-category runtime store accounting.
SYSTEM_DF_JSON = """{
  "containers" : {
    "active" : 0,
    "reclaimable" : 282157056,
    "sizeInBytes" : 282157056,
    "total" : 1
  },
  "images" : {
    "active" : 1,
    "reclaimable" : 14525538304,
    "sizeInBytes" : 15177728000,
    "total" : 14
  },
  "volumes" : {
    "active" : 0,
    "reclaimable" : 0,
    "sizeInBytes" : 0,
    "total" : 0
  }
}"""

K8S_NODES_JSON = """[{
  "configuration": {
    "creationDate": "2026-08-30T10:00:00Z",
    "id": "cp-abc",
    "image": {"reference": "docker.io/kindest/node:v1.35.5"},
    "labels": {"com.apple.container.plugin": "k8s"},
    "name": "my-cluster"
  },
  "id": "cp-abc",
  "status": {"state": "running"},
  "pluginRole": "control-plane"
}, {
  "configuration": {
    "creationDate": "2026-08-30T09:00:00Z",
    "id": "plain-xyz",
    "image": {"reference": "docker.io/library/alpine:latest"},
    "labels": {},
    "name": "plain-xyz"
  },
  "id": "plain-xyz",
  "status": {"state": "running"}
}]"""


# ------------------------------------------------------------------
# stats parsing (SPEC §2.1)
# ------------------------------------------------------------------


class TestStatsParser:
    def test_parse_single_sample(self) -> None:
        samples = OutputParser.stats(STATS_JSON)
        assert len(samples) == 1
        s = samples[0]
        assert s.id == "f634ad0e-abc"
        assert s.cpu_usage_usec == 2219
        assert s.memory_usage_bytes == 2764800
        assert s.memory_limit_bytes == 1073741824
        assert s.network_rx_bytes == 28040
        assert s.network_tx_bytes == 602
        assert s.block_read_bytes == 2596864
        assert s.block_write_bytes == 0
        assert s.num_processes == 1
        assert s.sampled_at > 0.0

    def test_parse_empty_array(self) -> None:
        assert OutputParser.stats("[]") == []

    def test_parse_empty_text(self) -> None:
        assert OutputParser.stats("") == []

    def test_missing_fields_default_to_zero(self) -> None:
        [s] = OutputParser.stats('[{"id": "x"}]')
        assert s.id == "x"
        assert s.cpu_usage_usec == 0
        assert s.memory_usage_bytes == 0
        assert s.num_processes == 0
        assert s.sampled_at > 0.0


class TestStatsSampleCpuPercent:
    def test_rate_between_two_samples(self) -> None:
        prev = StatsSample(id="x", cpu_usage_usec=0, sampled_at=0.0)
        cur = StatsSample(id="x", cpu_usage_usec=500_000, sampled_at=1.0)
        # 0.5 core-seconds of CPU over 1 s wall time = 50% of one core.
        assert cur.cpu_percent(prev) == pytest.approx(50.0)

    def test_zero_time_delta_guard(self) -> None:
        prev = StatsSample(id="x", cpu_usage_usec=1000, sampled_at=10.0)
        cur = StatsSample(id="x", cpu_usage_usec=2000, sampled_at=10.0)
        assert cur.cpu_percent(prev) == 0.0

    def test_negative_time_delta_guard(self) -> None:
        prev = StatsSample(id="x", cpu_usage_usec=5000, sampled_at=20.0)
        cur = StatsSample(id="x", cpu_usage_usec=5000, sampled_at=10.0)
        assert cur.cpu_percent(prev) == 0.0

    def test_no_cpu_advance_returns_zero(self) -> None:
        prev = StatsSample(id="x", cpu_usage_usec=1000, sampled_at=0.0)
        cur = StatsSample(id="x", cpu_usage_usec=1000, sampled_at=10.0)
        assert cur.cpu_percent(prev) == 0.0

    def test_clamped_to_100(self) -> None:
        prev = StatsSample(id="x", cpu_usage_usec=0, sampled_at=0.0)
        cur = StatsSample(id="x", cpu_usage_usec=1_000_000, sampled_at=1.0)
        assert cur.cpu_percent(prev) == 100.0

    def test_to_dict(self) -> None:
        s = StatsSample(id="x", cpu_usage_usec=2219, sampled_at=1.5)
        d = s.to_dict()
        assert d["id"] == "x"
        assert d["cpu_usage_usec"] == 2219
        assert d["sampled_at"] == 1.5


# ------------------------------------------------------------------
# network parsing (SPEC §2.5)
# ------------------------------------------------------------------


class TestNetworkListParser:
    def test_parse_single_network(self) -> None:
        nets = OutputParser.network_list(NETWORK_LIST_JSON)
        assert len(nets) == 1
        n = nets[0]
        assert isinstance(n, NetworkInfo)
        assert n.id == "default"
        assert n.name == "default"
        assert n.plugin == "container-network-vmnet"
        assert n.mode == "nat"
        assert n.subnet == "192.168.64.0/24"
        assert n.subnet_v6 == "fd8b:1877:bb8f:91bb::/64"
        assert n.gateway == "192.168.64.1"
        # raw carries the full dict, not just the parsed projection
        assert n.raw["id"] == "default"
        assert n.raw["status"]["ipv4Subnet"] == "192.168.64.0/24"
        assert n.raw["configuration"]["labels"] == {
            "com.apple.container.resource.role": "builtin"
        }

    def test_parse_empty_array(self) -> None:
        assert OutputParser.network_list("[]") == []

    def test_missing_status_defaults_to_empty(self) -> None:
        [n] = OutputParser.network_list('[{"id": "n1", "configuration": {"name": "n1"}}]')
        assert n.id == "n1"
        assert n.name == "n1"
        assert n.plugin == ""
        assert n.mode == ""
        assert n.subnet == ""
        assert n.subnet_v6 == ""
        assert n.gateway == ""


class TestNetworkInspectParser:
    def test_first_element_extraction(self) -> None:
        # inspect output is the same shape but arrives as a JSON array
        n = OutputParser.network_inspect(NETWORK_LIST_JSON)
        assert isinstance(n, NetworkInfo)
        assert n.id == "default"
        assert n.name == "default"
        assert n.plugin == "container-network-vmnet"
        assert n.mode == "nat"
        assert n.subnet == "192.168.64.0/24"
        assert n.subnet_v6 == "fd8b:1877:bb8f:91bb::/64"
        assert n.gateway == "192.168.64.1"

    def test_multiple_networks_uses_first(self) -> None:
        two = (
            '[{"id": "first", "configuration": {"name": "first"}},'
            ' {"id": "second", "configuration": {"name": "second"}}]'
        )
        n = OutputParser.network_inspect(two)
        assert n.id == "first"
        assert n.name == "first"

    def test_empty_array_returns_empty_info(self) -> None:
        n = OutputParser.network_inspect("[]")
        assert n.id == ""
        assert n.name == ""
        assert n.subnet == ""


# ------------------------------------------------------------------
# prune parsing (SPEC §2.4)
# ------------------------------------------------------------------


class TestPruneParser:
    def test_reclaimed_message_and_ids(self) -> None:
        out = "Reclaimed 282.1 MB in disk space\n<uuid1>\n<uuid2>\n"
        result = OutputParser.prune(out)
        assert isinstance(result, PruneResult)
        assert result.message == "Reclaimed 282.1 MB in disk space"
        assert result.ids == ["<uuid1>", "<uuid2>"]

    def test_empty_output(self) -> None:
        assert OutputParser.prune("") == PruneResult()

    def test_ids_without_reclaimed_line(self) -> None:
        result = OutputParser.prune("<uuid1>\n<uuid2>\n")
        assert result.ids == ["<uuid1>", "<uuid2>"]
        assert result.message == ""


# ------------------------------------------------------------------
# RunSpec network / publish / caps serialization
# ------------------------------------------------------------------


class TestRunSpecNetworkArgs:
    def test_serialization_order(self) -> None:
        spec = RunSpec(
            image="alpine:latest",
            network="victim-net",
            publish=["8080:80", "8443:443"],
            cap_add=["NET_ADMIN", "SYS_PTRACE"],
            cap_drop=["MKNOD"],
        )
        args = spec.to_cli_args()
        # network → publish → cap_add → cap_drop
        assert args[args.index("--network") + 1] == "victim-net"
        assert args.index("--network") < args.index("-p")
        assert args.index("-p") < args.index("--cap-add")
        assert args.index("--cap-add") < args.index("--cap-drop")
        # image comes after all flags
        assert args[-1] == "alpine:latest"

    def test_repeatable_flags(self) -> None:
        spec = RunSpec(
            image="alpine:latest",
            publish=["8080:80", "8443:443"],
            cap_add=["NET_ADMIN", "SYS_PTRACE"],
            cap_drop=["MKNOD", "SYS_CHROOT"],
        )
        args = spec.to_cli_args()
        pub_idx = [i for i, a in enumerate(args) if a == "-p"]
        cap_add_idx = [i for i, a in enumerate(args) if a == "--cap-add"]
        cap_drop_idx = [i for i, a in enumerate(args) if a == "--cap-drop"]
        assert len(pub_idx) == 2
        assert args[pub_idx[0] + 1] == "8080:80"
        assert args[pub_idx[1] + 1] == "8443:443"
        assert args[cap_add_idx[0] + 1] == "NET_ADMIN"
        assert args[cap_add_idx[1] + 1] == "SYS_PTRACE"
        assert args[cap_drop_idx[0] + 1] == "MKNOD"
        assert args[cap_drop_idx[1] + 1] == "SYS_CHROOT"

    def test_defaults_emit_nothing(self) -> None:
        args = RunSpec(image="alpine:latest").to_cli_args()
        assert "--network" not in args
        assert "-p" not in args
        assert "--cap-add" not in args
        assert "--cap-drop" not in args
        assert args == ["alpine:latest"]


# ------------------------------------------------------------------
# Capability gating (CliBackend._require)
# ------------------------------------------------------------------


class TestRequireGate:
    def _backend(self, runtime_features: dict[str, bool | str], version: str) -> CliBackend:
        caps = CapabilityManifest(
            container_binary="/usr/local/bin/container",
            container_version=version,
            runtime_features=runtime_features,
        )
        return CliBackend(caps)

    def test_present_feature_passes(self) -> None:
        backend = self._backend({"container_stats": True}, "1.2.2")
        backend._require("container_stats")  # no raise

    def test_missing_feature_raises(self) -> None:
        backend = self._backend({}, "0.1.0")
        with pytest.raises(CapabilityError) as excinfo:
            backend._require("container_stats")
        assert excinfo.value.code == "CAPABILITY_MISSING"
        assert excinfo.value.details["feature"] == "container_stats"
        assert excinfo.value.details["container_version"] == "0.1.0"
        assert "container_stats" in str(excinfo.value)

    def test_untested_feature_raises(self) -> None:
        backend = self._backend({"custom_networks": "untested"}, "1.2.2")
        with pytest.raises(CapabilityError):
            backend._require("custom_networks")


class TestCapabilityErrorExtension:
    def test_carries_feature_and_version(self) -> None:
        err = CapabilityError(
            message="Feature 'container_stats' is not available on this host",
            feature="container_stats",
            container_version="0.1.0",
        )
        assert err.code == "CAPABILITY_MISSING"
        assert err.details["feature"] == "container_stats"
        assert err.details["container_version"] == "0.1.0"

    def test_backward_compat_defaults(self) -> None:
        err = CapabilityError(feature="custom_networks", hint="start container system")
        assert err.details["feature"] == "custom_networks"
        assert err.details["hint"] == "start container system"
        assert "container_version" not in err.details


# ------------------------------------------------------------------
# container list-item parsing (container >= 1.x status-nested shape)
# ------------------------------------------------------------------


LIST_ITEM_JSON = """{
  "configuration": {
    "creationDate": "2026-08-21T15:58:35Z",
    "dns": {"nameservers": [], "options": [], "searchDomains": []},
    "id": "f634ad0e-366e-479a-b2b1-a124064a0e27",
    "image": {
      "descriptor": {
        "digest": "sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b"},


      "reference": "docker.io/library/alpine:latest"
    },
    "labels": {},
    "networks": [{"network": "default"}]
  },
  "id": "f634ad0e-366e-479a-b2b1-a124064a0e27",
  "status": {
    "networks": [{"hostname": "f634ad0e-366e-479a-b2b1-a124064a0e27",
                   "ipv4Address": "192.168.64.4/24", "ipv4Gateway": "192.168.64.1"}],
    "startedDate": "2026-08-21T15:58:35Z",
    "state": "running"
  }
}"""


class TestContainerItemParser:
    def test_running_item_fields(self) -> None:
        import json
        item = json.loads(LIST_ITEM_JSON)
        info = OutputParser.container_item(item)
        assert info.id == "f634ad0e-366e-479a-b2b1-a124064a0e27"
        # name comes from status.networks[0].hostname
        assert info.name == "f634ad0e-366e-479a-b2b1-a124064a0e27"
        assert info.image == "docker.io/library/alpine:latest"
        assert info.state.value == "running"

    def test_stopped_state(self) -> None:
        import json
        item = json.loads(LIST_ITEM_JSON)
        item["status"]["state"] = "stopped"
        item["status"]["networks"] = []
        info = OutputParser.container_item(item)
        assert info.state.value == "stopped"
        assert info.name == ""  # no hostname anywhere

    def test_unknown_state_coerces_to_unknown(self) -> None:
        import json
        item = json.loads(LIST_ITEM_JSON)
        item["status"]["state"] = "paused"  # not a known ContainerState
        info = OutputParser.container_item(item)
        assert info.state.value == "unknown"

    def test_missing_status_tolerated(self) -> None:
        item = {"configuration": {"id": "abc", "image": {"reference": "img"}}}
        info = OutputParser.container_item(item)
        assert info.id == "abc"
        assert info.state.value == "unknown"


# ------------------------------------------------------------------
# image list-item normalisation (container >= 1.x nested shape)
# ------------------------------------------------------------------


IMAGE_LIST_ITEM_JSON = """{
  "configuration": {
    "creationDate": "2026-06-16T00:00:15Z",
    "descriptor": {
      "digest": "sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b",
      "mediaType": "application/vnd.oci.image.index.v1+json",
                    "size": 9218},
    "name": "docker.io/library/alpine:latest"
  },
  "id": "28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b",
  "variants": []
}"""


class TestImageListItemParser:
    def test_nested_name_becomes_top_level_reference(self) -> None:
        import json
        item = OutputParser.image_list_item(json.loads(IMAGE_LIST_ITEM_JSON))
        assert item["reference"] == "docker.io/library/alpine:latest"
        assert item["name"] == "docker.io/library/alpine:latest"
        assert item["descriptor"]["digest"].startswith("sha256:28bd5fe8")
        assert item["id"].startswith("28bd5fe8")

    def test_v010_flat_shape_untouched(self) -> None:
        item = OutputParser.image_list_item(
            {"reference": "docker.io/library/alpine:latest",
             "descriptor": {"digest": "sha256:abc"}}
        )
        assert item["reference"] == "docker.io/library/alpine:latest"
        assert item["name"] == "docker.io/library/alpine:latest"
        assert item["descriptor"]["digest"] == "sha256:abc"

    def test_network_spec_labels_serialization(self) -> None:
        spec = NetworkSpec(
            name="lab", internal=True,
            labels={"owner": "minos", "tier": "analysis"},
        )
        args = spec.to_cli_args()
        assert "--internal" in args
        assert "--label" in args
        assert "owner=minos" in args
        assert "tier=analysis" in args
        assert args[0] == "lab"


# ------------------------------------------------------------------
# machine parsing (SPEC §2.1 — captured live on container 1.2.2)
# ------------------------------------------------------------------

MACHINE_LIST_JSON = """[{"createdDate": "2026-08-21T20:31:29Z", "status": "stopped",
  "diskSize": 1169178624, "default": true, "id": "daedalus-probe-m",
  "cpus": 8, "memory": 25769803776}]"""

MACHINE_INSPECT_JSON = """[{"cpus": 8, "createdDate": "2026-08-21T20:31:29Z",
  "diskSize": 1169178624, "homeMount": "rw", "id": "daedalus-probe-m",
  "image": {"descriptor": {}, "reference": "docker.io/library/alpine:3.22"},
  "memory": 25769803776, "platform": {"architecture": "arm64", "os": "linux"},
  "status": "stopped", "userSetup": {"gid": 20, "uid": 501, "username": "jay"}}]"""


class TestMachineListParser:
    def test_parse_literal_list(self) -> None:
        machines = OutputParser.machine_list(MACHINE_LIST_JSON)
        assert len(machines) == 1
        m = machines[0]
        assert isinstance(m, MachineInfo)
        assert m.id == "daedalus-probe-m"
        assert m.status == "stopped"
        assert m.cpus == 8
        # memory and diskSize are bytes on the wire
        assert m.memory_bytes == 25769803776
        assert m.disk_size_bytes == 1169178624
        assert m.default is True
        assert m.created_date == "2026-08-21T20:31:29Z"
        # list shape carries no inspect-only fields
        assert m.home_mount == ""
        assert m.image_ref == ""
        assert m.platform_arch == ""
        assert m.platform_os == ""
        assert m.user_username == ""
        assert m.user_uid == 0
        assert m.user_gid == 0
        # raw carries the full dict, not just the parsed projection
        assert m.raw["id"] == "daedalus-probe-m"
        assert m.raw["diskSize"] == 1169178624

    def test_parse_empty_array(self) -> None:
        assert OutputParser.machine_list("[]") == []

    def test_parse_empty_text(self) -> None:
        assert OutputParser.machine_list("") == []

    def test_missing_fields_default(self) -> None:
        [m] = OutputParser.machine_list('[{"id": "m1"}]')
        assert m.id == "m1"
        assert m.status == ""
        assert m.cpus == 0
        assert m.memory_bytes == 0
        assert m.disk_size_bytes == 0
        assert m.default is False
        assert m.created_date == ""


class TestMachineInspectParser:
    def test_parse_literal_inspect(self) -> None:
        m = OutputParser.machine_inspect(MACHINE_INSPECT_JSON)
        assert isinstance(m, MachineInfo)
        # shared list fields
        assert m.id == "daedalus-probe-m"
        assert m.status == "stopped"
        assert m.cpus == 8
        assert m.memory_bytes == 25769803776
        assert m.disk_size_bytes == 1169178624
        assert m.created_date == "2026-08-21T20:31:29Z"
        # inspect-only fields merged from the richer shape
        assert m.home_mount == "rw"
        assert m.image_ref == "docker.io/library/alpine:3.22"
        assert m.platform_arch == "arm64"
        assert m.platform_os == "linux"
        assert m.user_username == "jay"
        assert m.user_uid == 501
        assert m.user_gid == 20

    def test_array_uses_first_element(self) -> None:
        two = (
            '[{"id": "first", "status": "stopped"},'
            ' {"id": "second", "status": "running"}]'
        )
        m = OutputParser.machine_inspect(two)
        assert m.id == "first"
        assert m.status == "stopped"

    def test_empty_array_returns_empty_info(self) -> None:
        m = OutputParser.machine_inspect("[]")
        assert m.id == ""
        assert m.status == ""
        assert m.cpus == 0
        assert m.memory_bytes == 0
        assert m.home_mount == ""
        assert m.image_ref == ""
        assert m.platform_arch == ""
        assert m.platform_os == ""
        assert m.user_username == ""
        assert m.user_uid == 0
        assert m.user_gid == 0

    def test_machine_id_first_nonempty_line(self) -> None:
        assert OutputParser.machine_id("daedalus-probe-m\n") == "daedalus-probe-m"
        assert OutputParser.machine_id("") == ""


class TestMachineSpecSerialization:
    def test_to_cli_args_order_and_image_last(self) -> None:
        spec = MachineCreateSpec(
            image="alpine:3.22",
            name="test-m",
            set_default=True,
            no_boot=True,
            cpus=8,
            memory="2G",
            home_mount="rw",
            virtualization=True,
            kernel="/tmp/vmlinux",
            arch="arm64",
            os="linux",
            platform="apple-silicon",
            scheme="http",
        )
        args = spec.to_cli_args()
        assert args == [
            "--name", "test-m",
            "--set-default",
            "--no-boot",
            "--cpus", "8",
            "--memory", "2G",
            "--home-mount", "rw",
            "--virtualization",
            "--kernel", "/tmp/vmlinux",
            "--arch", "arm64",
            "--os", "linux",
            "--platform", "apple-silicon",
            "--scheme", "http",
            "alpine:3.22",
        ]

    def test_defaults_emit_image_only(self) -> None:
        assert MachineCreateSpec(image="alpine:latest").to_cli_args() == [
            "alpine:latest",
        ]

    def test_to_set_args_only_non_none(self) -> None:
        settings = MachineSetSettings(
            cpus=2, memory="4G", home_mount="ro",
            virtualization=True, kernel="/tmp/vmlinux",
        )
        assert settings.to_set_args() == [
            "cpus=2", "memory=4G", "home-mount=ro",
            "virtualization=true", "kernel=/tmp/vmlinux",
        ]

    def test_to_set_args_virtualization_false(self) -> None:
        settings = MachineSetSettings(virtualization=False)
        assert settings.to_set_args() == ["virtualization=false"]

    def test_to_set_args_empty_settings(self) -> None:
        assert MachineSetSettings().to_set_args() == []

    def test_to_set_args_kernel_empty_resets(self) -> None:
        # kernel="" is meaningful — the CLI resets the kernel
        assert MachineSetSettings(kernel="").to_set_args() == ["kernel="]


# ------------------------------------------------------------------
# volume parsing (SPEC §2 — captured live on container 1.2.2)
# ------------------------------------------------------------------


class TestVolumeListParser:
    def test_parse_literal_list(self) -> None:
        volumes = OutputParser.volume_list(VOLUME_LIST_JSON)
        assert len(volumes) == 1
        v = volumes[0]
        assert isinstance(v, VolumeInfo)
        assert v.id == "daedalus-probe-v"
        assert v.name == "daedalus-probe-v"
        assert v.driver == "local"
        assert v.format == "ext4"
        assert v.size_bytes == 104857600
        assert v.size_spec == "100M"          # from options.size
        assert v.source == (
            "/Users/jay/Library/Application Support/com.apple.container/"
            "volumes/daedalus-probe-v/volume.img"
        )
        assert v.created_date == "2026-08-22T06:10:50Z"
        assert v.labels == {}
        assert v.options == {"size": "100M"}
        # raw carries the full dict, not just the parsed projection
        assert v.raw["id"] == "daedalus-probe-v"
        assert v.raw["configuration"]["sizeInBytes"] == 104857600

    def test_parse_empty_array(self) -> None:
        assert OutputParser.volume_list("[]") == []

    def test_parse_empty_text(self) -> None:
        assert OutputParser.volume_list("") == []

    def test_missing_configuration_defaults(self) -> None:
        [v] = OutputParser.volume_list('[{"id": "v1"}]')
        assert v.id == "v1"
        assert v.name == ""
        assert v.driver == ""
        assert v.format == ""
        assert v.size_bytes == 0
        assert v.size_spec == ""
        assert v.source == ""
        assert v.created_date == ""
        assert v.labels == {}
        assert v.options == {}


class TestVolumeInspectParser:
    def test_parse_literal_inspect(self) -> None:
        # inspect output is the same shape but arrives as a JSON array
        v = OutputParser.volume_inspect(VOLUME_LIST_JSON)
        assert isinstance(v, VolumeInfo)
        assert v.id == "daedalus-probe-v"
        assert v.name == "daedalus-probe-v"
        assert v.driver == "local"
        assert v.format == "ext4"
        assert v.size_bytes == 104857600
        assert v.size_spec == "100M"
        assert v.source.endswith("volumes/daedalus-probe-v/volume.img")
        assert v.options == {"size": "100M"}

    def test_multiple_volumes_uses_first(self) -> None:
        two = (
            '[{"id": "first", "configuration": {"name": "first"}},'
            ' {"id": "second", "configuration": {"name": "second"}}]'
        )
        v = OutputParser.volume_inspect(two)
        assert v.id == "first"
        assert v.name == "first"

    def test_empty_array_returns_empty_info(self) -> None:
        v = OutputParser.volume_inspect("[]")
        assert v.id == ""
        assert v.name == ""
        assert v.driver == ""
        assert v.size_bytes == 0
        assert v.size_spec == ""

    def test_volume_id_first_nonempty_line(self) -> None:
        assert OutputParser.volume_id("daedalus-probe-v\n") == "daedalus-probe-v"
        assert OutputParser.volume_id("") == ""


class TestVolumeSpecSerialization:
    def test_to_cli_args_order_and_name_last(self) -> None:
        spec = VolumeSpec(
            name="evidence-01",
            size="100M",
            labels={"owner": "daedalus", "tier": "evidence"},
            options={"fs": "ext4"},
        )
        assert spec.to_cli_args() == [
            "--label", "owner=daedalus",
            "--label", "tier=evidence",
            "--opt", "fs=ext4",
            "-s", "100M",
            "evidence-01",
        ]

    def test_defaults_emit_name_only(self) -> None:
        assert VolumeSpec(name="evidence-01").to_cli_args() == ["evidence-01"]


# ------------------------------------------------------------------
# k8s node filtering (SPEC phase 8 §2 — container 1.3.1)
# ------------------------------------------------------------------


class TestK8sNodesParser:
    def test_filters_to_k8s_plugin_nodes(self) -> None:
        nodes = OutputParser.k8s_nodes(K8S_NODES_JSON)
        assert len(nodes) == 1
        node = nodes[0]
        # the plain container is dropped, the k8s node survives raw
        assert node["id"] == "cp-abc"
        assert node["pluginRole"] == "control-plane"
        assert node["configuration"]["labels"]["com.apple.container.plugin"] == "k8s"
        assert node["status"]["state"] == "running"

    def test_empty_array(self) -> None:
        assert OutputParser.k8s_nodes("[]") == []

    def test_empty_text(self) -> None:
        assert OutputParser.k8s_nodes("") == []

    def test_missing_labels_filtered_out(self) -> None:
        data = '[{"id": "x", "configuration": {"labels": {}}}, {"id": "y"}]'
        assert OutputParser.k8s_nodes(data) == []

    def test_labels_without_plugin_key_filtered_out(self) -> None:
        data = '[{"id": "x", "configuration": {"labels": {"tier": "prod"}}}]'
        assert OutputParser.k8s_nodes(data) == []


class TestClusterCreateSpecSerialization:
    def test_to_cli_args_flag_order(self) -> None:
        spec = ClusterCreateSpec(
            name="lab",
            node_image="docker.io/kindest/node:v1.35.5",
            cpus=4,
            memory="8G",
            remove=True,
            scheme="http",
        )
        # name → node-image → scheme → rm → -c → -m; no positional arg
        assert spec.to_cli_args() == [
            "--name", "lab",
            "--node-image", "docker.io/kindest/node:v1.35.5",
            "--scheme", "http",
            "--rm",
            "-c", "4",
            "-m", "8G",
        ]

    def test_defaults_emit_name_only(self) -> None:
        assert ClusterCreateSpec(name="lab").to_cli_args() == ["--name", "lab"]

    def test_max_concurrent_downloads_not_emitted(self) -> None:
        # registry-level plumbing (CLI default: 3) — deliberately absent
        args = ClusterCreateSpec(name="lab").to_cli_args()
        assert "--max-concurrent-downloads" not in args


# ------------------------------------------------------------------
# RunSpec --read-only serialization
# ------------------------------------------------------------------


class TestRunSpecReadOnly:
    def test_false_omits_flag(self) -> None:
        args = RunSpec(image="alpine:latest").to_cli_args()
        assert "--read-only" not in args
        assert args == ["alpine:latest"]

    def test_true_emits_flag(self) -> None:
        args = RunSpec(image="alpine:latest", read_only=True).to_cli_args()
        assert "--read-only" in args

    def test_ordering_after_volume_flags(self) -> None:
        spec = RunSpec(image="alpine:latest", volumes=["data:/data"], read_only=True)
        args = spec.to_cli_args()
        assert args.index("--volume") < args.index("--read-only")
        assert args.index("--read-only") < args.index("alpine:latest")


# ------------------------------------------------------------------
# RunSpec --init-image / --ssh serialization (container 1.3.1)
# ------------------------------------------------------------------


class TestRunSpecInitImageSsh:
    def test_defaults_emit_nothing(self) -> None:
        args = RunSpec(image="alpine:latest").to_cli_args()
        assert "--init-image" not in args
        assert "--ssh" not in args
        assert args == ["alpine:latest"]

    def test_init_image_emitted(self) -> None:
        args = RunSpec(image="alpine:latest", init_image="trace-init:latest").to_cli_args()
        assert args[args.index("--init-image") + 1] == "trace-init:latest"
        assert args.index("--init-image") < args.index("alpine:latest")

    def test_ssh_emitted(self) -> None:
        args = RunSpec(image="alpine:latest", ssh=True).to_cli_args()
        assert "--ssh" in args
        assert args.index("--ssh") < args.index("alpine:latest")

    def test_ordering_init_image_then_ssh_before_image(self) -> None:
        spec = RunSpec(image="alpine:latest", init_image="i:1", ssh=True)
        args = spec.to_cli_args()
        assert args.index("--init-image") < args.index("--ssh") < args.index("alpine:latest")


# ------------------------------------------------------------------
# RunSpec phase-7 completion (SPEC phase 7 §3 — container 1.3.1)
# ------------------------------------------------------------------


class TestRunSpecCompletion:
    def test_defaults_emit_nothing(self) -> None:
        args = RunSpec(image="alpine:latest").to_cli_args()
        for flag in (
            "--init", "--kernel-arg", "--masked-path", "--read-only-path",
            "--rosetta", "--runtime", "--shm-size", "--ulimit",
            "--publish-socket", "--virtualization", "--platform", "--ssh",
        ):
            assert flag not in args
        assert args == ["alpine:latest"]

    def test_every_new_field_emitted_when_set(self) -> None:
        spec = RunSpec(
            image="alpine:latest",
            init=True,
            kernel_arg=["tracing.bpf=1"],
            masked_path=["/proc/kcore"],
            read_only_path=["/var/run"],
            rosetta=True,
            runtime="apple-silicon",
            shm_size="64M",
            ulimit=["nofile=1024"],
            publish_socket=["/var/run/docker.sock:/run/docker.sock"],
            virtualization=True,
            platform="linux/arm64",
        )
        args = spec.to_cli_args()
        assert "--init" in args
        assert args[args.index("--kernel-arg") + 1] == "tracing.bpf=1"
        assert args[args.index("--masked-path") + 1] == "/proc/kcore"
        assert args[args.index("--read-only-path") + 1] == "/var/run"
        assert "--rosetta" in args
        assert args[args.index("--runtime") + 1] == "apple-silicon"
        assert args[args.index("--shm-size") + 1] == "64M"
        assert args[args.index("--ulimit") + 1] == "nofile=1024"
        assert (
            args[args.index("--publish-socket") + 1]
            == "/var/run/docker.sock:/run/docker.sock"
        )
        assert "--virtualization" in args
        assert args[args.index("--platform") + 1] == "linux/arm64"
        assert args[-1] == "alpine:latest"

    def test_repeatable_flags_emit_in_order(self) -> None:
        spec = RunSpec(
            image="alpine:latest",
            kernel_arg=["k1=v1", "k2=v2"],
            masked_path=["/a", "/b"],
            read_only_path=["/c", "/d"],
            ulimit=["nofile=1024", "cpu=2"],
            publish_socket=["s1", "s2"],
        )
        args = spec.to_cli_args()
        for flag, values in (
            ("--kernel-arg", ["k1=v1", "k2=v2"]),
            ("--masked-path", ["/a", "/b"]),
            ("--read-only-path", ["/c", "/d"]),
            ("--ulimit", ["nofile=1024", "cpu=2"]),
            ("--publish-socket", ["s1", "s2"]),
        ):
            idxs = [i for i, a in enumerate(args) if a == flag]
            assert len(idxs) == len(values)
            assert [args[i + 1] for i in idxs] == values

    def test_emission_order(self) -> None:
        spec = RunSpec(
            image="alpine:latest",
            shm_size="64M",
            ulimit=["nofile=1024"],
            masked_path=["/proc/kcore"],
            read_only=True,
            read_only_path=["/var/run"],
            platform="linux/arm64",
            os="linux",
            arch="arm64",
            runtime="apple-silicon",
            init=True,
            init_image="init:1",
            kernel_arg=["k=v"],
            rosetta=True,
            virtualization=True,
            network="victim-net",
            publish=["8080:80"],
            publish_socket=["/sock:/sock"],
            ssh=True,
        )
        args = spec.to_cli_args()
        # resources → filesystem → platform → runtime → network → ssh → image
        assert (
            args.index("--shm-size")
            < args.index("--ulimit")
            < args.index("--masked-path")
        )
        assert args.index("--masked-path") < args.index("--read-only")
        # read_only_path immediately follows the --read-only flag
        assert args.index("--read-only-path") == args.index("--read-only") + 1
        assert args.index("--read-only-path") < args.index("--platform")
        # platform precedes os/arch
        assert args.index("--platform") < args.index("--os") < args.index("--arch")
        # runtime/instrumentation section: after platform, before network
        assert args.index("--arch") < args.index("--runtime")
        assert (
            args.index("--runtime")
            < args.index("--init")
            < args.index("--init-image")
            < args.index("--kernel-arg")
            < args.index("--rosetta")
            < args.index("--virtualization")
        )
        assert args.index("--virtualization") < args.index("--network")
        # publish_socket after publish; ssh after the network section
        assert args.index("-p") < args.index("--publish-socket") < args.index("--ssh")
        assert args.index("--ssh") < args.index("alpine:latest")

    def test_platform_takes_precedence_over_os_arch(self) -> None:
        spec = RunSpec(
            image="alpine:latest", platform="linux/x86_64", os="linux", arch="x86_64",
        )
        args = spec.to_cli_args()
        assert args[args.index("--platform") + 1] == "linux/x86_64"
        assert args.index("--platform") < args.index("--os") < args.index("--arch")


# ------------------------------------------------------------------
# BuildSpec phase-7 completion (SPEC phase 7 §2 — container 1.3.1)
# ------------------------------------------------------------------


class TestBuildSpecCompletion:
    def test_defaults_emit_context_only(self) -> None:
        args = BuildSpec().to_cli_args()
        assert "--secret" not in args
        assert "--ssh" not in args
        assert "--pull" not in args
        assert args == ["."]

    def test_secret_ssh_pull_vsock_emitted(self) -> None:
        spec = BuildSpec(
            secret=["id=mysecret,src=/tmp/secret", "id=other,src=/tmp/other"],
            ssh=True,
            pull=True,
            vsock_port=61234,
        )
        args = spec.to_cli_args()
        idxs = [i for i, a in enumerate(args) if a == "--secret"]
        assert [args[i + 1] for i in idxs] == [
            "id=mysecret,src=/tmp/secret",
            "id=other,src=/tmp/other",
        ]
        assert "--ssh" in args
        assert "--pull" in args
        assert args[args.index("--vsock-port") + 1] == "61234"
        # all flags precede the build context
        assert args[-1] == "."

    def test_flag_order_secret_ssh_pull_before_context(self) -> None:
        spec = BuildSpec(secret=["a=1"], ssh=True, pull=True, context="src/")
        args = spec.to_cli_args()
        assert (
            args.index("--secret")
            < args.index("--ssh")
            < args.index("--pull")
            < args.index("src/")
        )


# ------------------------------------------------------------------
# Wire-level verb extensions (no daemon — _run_cli_impl is faked)
# ------------------------------------------------------------------


class _RecordingCli:
    """Fake ``_run_cli_impl`` capturing ``(args, kwargs)`` per invocation."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self.stdout = ""  # scripted stdout for echo/print-back verbs
        self.exit_code = 0  # scripted exit code (e.g. 64 for usage errors)

    async def __call__(
        self, *args: str, **kwargs: Any,
    ) -> tuple[int, str, str]:
        self.calls.append((args, kwargs))
        return (self.exit_code, self.stdout, "")


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> _RecordingCli:
    """Patch ``_run_cli_impl`` so CliBackend verbs are testable without a daemon."""
    recorder = _RecordingCli()
    monkeypatch.setattr("daedalus.core.cli_backend._run_cli_impl", recorder)
    return recorder


def _backend(runtime_features: dict[str, bool | str] | None = None) -> CliBackend:
    return CliBackend(
        CapabilityManifest(
            container_binary="/usr/local/bin/container",
            container_version="1.3.1",
            runtime_features=runtime_features or {},
        )
    )


class TestExecWireOptions:
    """CliBackend.exec ``-d`` / ``--ulimit`` emission (SPEC phase 7 §2)."""

    async def test_defaults_emit_no_new_flags(self, rec: _RecordingCli) -> None:
        result = await _backend().exec("c1", ["echo", "hi"])
        assert result.exit_code == 0
        (cmd, _), = rec.calls
        assert "-d" not in cmd
        assert "--ulimit" not in cmd

    async def test_detached_emits_d(self, rec: _RecordingCli) -> None:
        await _backend().exec("c1", ["echo", "hi"], detached=True)
        (cmd, _), = rec.calls
        assert "-d" in cmd
        assert cmd[cmd.index("-d")] == "-d"

    async def test_ulimit_repeatable(self, rec: _RecordingCli) -> None:
        await _backend().exec(
            "c1", ["echo", "hi"], ulimit=["nofile=1024", "cpu=2"],
        )
        (cmd, _), = rec.calls
        idxs = [i for i, a in enumerate(cmd) if a == "--ulimit"]
        assert len(idxs) == 2
        assert [cmd[i + 1] for i in idxs] == ["nofile=1024", "cpu=2"]
        # argv is the tail of the command
        assert cmd[-2:] == ("echo", "hi")


class TestStopDeleteWire:
    """CliBackend.stop/delete ``-a`` (SPEC phase 7 §2)."""

    async def test_stop_all(self, rec: _RecordingCli) -> None:
        await _backend().stop("", all=True)
        (cmd, _), = rec.calls
        assert cmd == ("stop", "-a")

    async def test_stop_defaults(self, rec: _RecordingCli) -> None:
        await _backend().stop("c1")
        (cmd, _), = rec.calls
        assert cmd == ("stop", "c1", "-t", "10")

    async def test_stop_signal(self, rec: _RecordingCli) -> None:
        await _backend().stop("c1", signal="TERM")
        (cmd, _), = rec.calls
        assert cmd == ("stop", "c1", "-t", "10", "--signal", "TERM")

    async def test_delete_all(self, rec: _RecordingCli) -> None:
        await _backend().delete("", all=True)
        (cmd, _), = rec.calls
        assert cmd == ("delete", "-a")

    async def test_delete_force(self, rec: _RecordingCli) -> None:
        await _backend().delete("c1", force=True)
        (cmd, _), = rec.calls
        assert cmd == ("delete", "c1", "--force")

    async def test_delete_defaults(self, rec: _RecordingCli) -> None:
        await _backend().delete("c1")
        (cmd, _), = rec.calls
        assert cmd == ("delete", "c1")


class TestMachineRunWire:
    """CliBackend.machine_run new flags (SPEC phase 7 §2)."""

    async def test_new_flags_emitted(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_machines": True})
        result = await backend.machine_run(
            "m1", ["uname", "-m"],
            root=True,
            ulimit=["nofile=1024"],
            detached=True,
            tty=True,
            user="root",
            workdir="/tmp",
        )
        assert result.exit_code == 0
        (cmd, _), = rec.calls
        assert cmd[:4] == ("machine", "run", "-n", "m1")
        assert "--root" in cmd
        assert cmd[cmd.index("--ulimit") + 1] == "nofile=1024"
        assert "-d" in cmd
        assert "-t" in cmd
        assert cmd[cmd.index("-u") + 1] == "root"
        assert cmd[cmd.index("-w") + 1] == "/tmp"
        assert cmd[-2:] == ("uname", "-m")

    async def test_defaults_emit_no_new_flags(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_machines": True})
        await backend.machine_run("m1", ["id"])
        (cmd, _), = rec.calls
        assert cmd == ("machine", "run", "-n", "m1", "id")

    async def test_ulimit_repeatable(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_machines": True})
        await backend.machine_run("m1", ["id"], ulimit=["nofile=1024", "cpu=2"])
        (cmd, _), = rec.calls
        idxs = [i for i, a in enumerate(cmd) if a == "--ulimit"]
        assert [cmd[i + 1] for i in idxs] == ["nofile=1024", "cpu=2"]


class TestMachineLogsWire:
    """CliBackend.machine_logs --boot/-f/-n (SPEC phase 7 §2)."""

    async def test_boot_and_tail_emitted(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_machines": True})
        out = await backend.machine_logs("m1", boot=True, tail=50)
        assert out == ""
        (cmd, _), = rec.calls
        assert cmd == ("machine", "logs", "m1", "--boot", "-n", "50")

    async def test_defaults_emit_id_only(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_machines": True})
        await backend.machine_logs("m1")
        (cmd, _), = rec.calls
        assert cmd == ("machine", "logs", "m1")


class TestRegistryLoginWire:
    """CliBackend.registry_login -u / --password-stdin (SPEC phase 7 §2)."""

    async def test_username_scheme_password_stdin(self, rec: _RecordingCli) -> None:
        await _backend().registry_login(
            "reg.example.com",
            username="jay",
            password_stdin="s3cr3t",
            scheme="http",
        )
        (cmd, kwargs), = rec.calls
        assert cmd == (
            "registry", "login", "-u", "jay", "--scheme", "http",
            "--password-stdin", "reg.example.com",
        )
        assert kwargs["input_text"] == "s3cr3t"

    async def test_plain_login_has_no_stdin_input(self, rec: _RecordingCli) -> None:
        await _backend().registry_login("reg.example.com")
        (cmd, kwargs), = rec.calls
        assert cmd == ("registry", "login", "reg.example.com")
        assert "input_text" not in kwargs


# ------------------------------------------------------------------
# exec wire order (container >= 1.x: options precede the container id)
# ------------------------------------------------------------------


class TestExecWireOrder:
    def test_options_precede_container_id(self) -> None:
        """Option flags must come BEFORE <id> — post-id args are the command.

        Regression for the 1.3.1 wire contract: `container exec -t <id> ...`
        works, `<id> -t ...` is parsed as the executable.
        """
        captured: dict[str, object] = {}

        async def fake_impl(*args: str, **kw: object) -> tuple[int, str, str]:
            captured["args"] = args
            captured["kw"] = kw
            return 0, "ok", ""

        import daedalus.core.cli_backend as cb

        orig = cb._run_cli_impl
        cb._run_cli_impl = fake_impl  # type: ignore[assignment]
        try:
            import asyncio

            async def go() -> None:
                backend = CliBackend(CapabilityManifest())
                await backend.exec(
                    "cid-123", ["uname", "-m"],
                    tty=True, detached=True, user="root", workdir="/",
                    env={"A": "1"}, ulimit=["nofile=1024"],
                )

            asyncio.run(go())
        finally:
            cb._run_cli_impl = orig  # type: ignore[assignment]

        args = captured["args"]
        assert isinstance(args, tuple)
        idx = args.index("cid-123")
        flags = [a for a in args[:idx] if str(a).startswith("-")]
        # flags only (value args are filtered out by the startswith("-"))
        assert flags == [
            "--tty", "--interactive", "-d", "--user",
            "--ulimit", "--workdir", "--env",
        ]
        assert list(args[idx + 1:]) == ["uname", "-m"]


# ------------------------------------------------------------------
# k8s plugin wire verbs (SPEC phase 8 §2 — container 1.3.1)
# ------------------------------------------------------------------


class TestK8sWire:
    async def test_k8s_create(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_k8s": True})
        name = await backend.k8s_create(
            ClusterCreateSpec(name="lab", cpus=2, remove=True),
        )
        assert name == "lab"
        (cmd, _), = rec.calls
        assert cmd == ("k8s", "create", "--name", "lab", "--rm", "-c", "2")

    async def test_k8s_create_echoes_name(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_k8s": True})
        rec.stdout = "lab\n"
        name = await backend.k8s_create(ClusterCreateSpec(name="lab"))
        assert name == "lab"

    async def test_k8s_list(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_k8s": True})
        assert await backend.k8s_list() == []
        (cmd, _), = rec.calls
        # the k8s CLI has no JSON output — the raw container list is read
        assert cmd == ("list", "--format", "json")

    async def test_k8s_delete(self, rec: _RecordingCli) -> None:
        await _backend({"container_k8s": True}).k8s_delete("lab")
        (cmd, _), = rec.calls
        assert cmd == ("k8s", "delete", "--name", "lab")

    async def test_k8s_start(self, rec: _RecordingCli) -> None:
        await _backend({"container_k8s": True}).k8s_start("lab")
        (cmd, _), = rec.calls
        assert cmd == ("k8s", "start", "--name", "lab")

    async def test_k8s_load_image(self, rec: _RecordingCli) -> None:
        await _backend({"container_k8s": True}).k8s_load_image(
            "lab", "demo:latest", platform="linux/arm64",
        )
        (cmd, _), = rec.calls
        assert cmd == (
            "k8s", "load-image", "--name", "lab",
            "demo:latest", "--platform", "linux/arm64",
        )

    async def test_k8s_load_image_no_platform(self, rec: _RecordingCli) -> None:
        await _backend({"container_k8s": True}).k8s_load_image("lab", "demo:latest")
        (cmd, _), = rec.calls
        assert cmd == ("k8s", "load-image", "--name", "lab", "demo:latest")

    async def test_k8s_write_config_to_path(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_k8s": True})
        assert await backend.k8s_write_config("lab", kubeconfig="/tmp/kube") == "/tmp/kube"
        (cmd, _), = rec.calls
        assert cmd == ("k8s", "write-config", "--name", "lab", "--kubeconfig", "/tmp/kube")

    async def test_k8s_write_config_returns_printed_text(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[str, ...]] = []

        async def fake(*args: str, **kwargs: Any) -> tuple[int, str, str]:
            calls.append(args)
            return 0, "apiVersion: v1\nkind: Config\n", ""

        monkeypatch.setattr("daedalus.core.cli_backend._run_cli_impl", fake)
        backend = _backend({"container_k8s": True})
        out = await backend.k8s_write_config("lab")
        assert out == "apiVersion: v1\nkind: Config\n"
        assert calls == [("k8s", "write-config", "--name", "lab")]


class TestK8sGate:
    """Every k8s verb requires the ``container_k8s`` runtime feature."""

    async def test_create_raises_without_gate(self, rec: _RecordingCli) -> None:
        backend = _backend({})  # container_k8s absent
        with pytest.raises(CapabilityError) as excinfo:
            await backend.k8s_create(ClusterCreateSpec(name="lab"))
        assert excinfo.value.details["feature"] == "container_k8s"
        assert rec.calls == []

    async def test_list_raises_without_gate(self, rec: _RecordingCli) -> None:
        backend = _backend({})
        with pytest.raises(CapabilityError):
            await backend.k8s_list()

    async def test_untested_feature_raises(self, rec: _RecordingCli) -> None:
        backend = _backend({"container_k8s": "untested"})
        with pytest.raises(CapabilityError):
            await backend.k8s_write_config("lab")


# ------------------------------------------------------------------
# system property parsing (SPEC phase 9 §2 — captured live on 1.3.1)
# ------------------------------------------------------------------


class TestSystemPropertiesParser:
    def test_flattens_toml_to_dotted_strings(self) -> None:
        props = OutputParser.system_properties(PROPERTY_LIST_TOML)
        assert props["build.cpus"] == "2"
        assert props["build.image"] == (
            "ghcr.io/apple/container-builder-shim/builder:0.13.1"
        )
        assert props["build.memory"] == "2048mb"
        assert props["build.rosetta"] == "true"   # bool → "true"
        assert props["container.cpus"] == "4"
        assert props["kernel.binaryPath"] == (
            "opt/kata/share/kata-containers/vmlinux-6.18.35-197-debug"
        )
        assert props["kernel.digest"].startswith("sha256:8736c054")
        assert props["machine.cpus"] == "8"
        assert props["machine.homeMount"] == "rw"
        assert props["machine.memory"] == "24gb"
        assert props["machine.virtualization"] == "false"
        assert props["registry.domain"] == "docker.io"
        assert props["vminit.image"] == (
            "ghcr.io/apple/containerization/vminit:0.42.0"
        )
        # empty tables ([dns], [network]) contribute no keys
        assert "dns." not in "".join(props)
        assert len(props) == 15

    def test_empty_text(self) -> None:
        assert OutputParser.system_properties("") == {}

    def test_unparseable_text_returns_empty(self) -> None:
        assert OutputParser.system_properties("not [valid toml") == {}


class TestSystemVersionParser:
    def test_array_keyed_by_app_name(self) -> None:
        version = OutputParser.system_version(SYSTEM_VERSION_JSON)
        assert set(version) == {"container", "container-apiserver"}
        assert version["container"]["version"] == "1.3.1"
        assert version["container"]["buildType"] == "release"
        assert version["container"]["commit"] == (
            "a9a62e28f6beb88940122a3d7b286f2d5ae8053a"
        )
        assert version["container-apiserver"]["version"].startswith(
            "container-apiserver version 1.3.1"
        )

    def test_empty_array(self) -> None:
        assert OutputParser.system_version("[]") == {}

    def test_empty_text(self) -> None:
        assert OutputParser.system_version("") == {}

    def test_single_object_accepted(self) -> None:
        single = '{"appName": "container", "version": "1.3.1"}'
        assert OutputParser.system_version(single)["container"]["version"] == "1.3.1"

    def test_entries_without_app_name_dropped(self) -> None:
        data = '[{"appName": "container", "version": "1.3.1"}, {"version": "2.0"}]'
        assert set(OutputParser.system_version(data)) == {"container"}


class TestSystemDfJsonParser:
    def test_live_shape_maps_used_from_size_in_bytes(self) -> None:
        df = OutputParser.system_df_json(SYSTEM_DF_JSON)
        # 282157056 (containers) + 15177728000 (images) + 0 (volumes)
        assert df["used"] == 15459885056
        assert df["total"] == df["used"]  # no capacity figure in the JSON
        assert df["containers"] == {
            "active": 0, "reclaimable": 282157056,
            "sizeInBytes": 282157056, "total": 1,
        }
        assert df["images"]["sizeInBytes"] == 15177728000
        assert df["images"]["active"] == 1
        assert df["volumes"]["total"] == 0

    def test_empty_text(self) -> None:
        assert OutputParser.system_df_json("") == {}

    def test_non_json_returns_empty(self) -> None:
        assert OutputParser.system_df_json("CONTAINER ID  IMAGE") == {}

    def test_missing_categories_tolerated(self) -> None:
        partial = '{"images": {"sizeInBytes": 100}}'
        df = OutputParser.system_df_json(partial)
        assert df["used"] == 100
        assert df["containers"] == {}
        assert df["volumes"] == {}


# ------------------------------------------------------------------
# system property / version / df wire verbs (no daemon — rec fakes CLI)
# ------------------------------------------------------------------


class TestSystemPropertyWire:
    async def test_list_command_and_parse(self, rec: _RecordingCli) -> None:
        backend = _backend({"system_properties": True})
        rec.stdout = PROPERTY_LIST_TOML
        props = await backend.system_property_list()
        assert props["build.cpus"] == "2"
        (cmd, _), = rec.calls
        # default --format (toml) — the widest-compatible wire
        assert cmd == ("system", "property", "list")

    async def test_list_requires_gate(self, rec: _RecordingCli) -> None:
        backend = _backend({})
        with pytest.raises(CapabilityError) as excinfo:
            await backend.system_property_list()
        assert excinfo.value.details["feature"] == "system_properties"
        assert rec.calls == []

    async def test_get_requires_gate(self, rec: _RecordingCli) -> None:
        backend = _backend({})
        with pytest.raises(CapabilityError):
            await backend.system_property_get("build.cpus")

    async def test_set_requires_gate(self, rec: _RecordingCli) -> None:
        backend = _backend({})
        with pytest.raises(CapabilityError):
            await backend.system_property_set("build.cpus", "4")

    async def test_get_unavailable_on_131_host(self, rec: _RecordingCli) -> None:
        """1.3.1 has no property get — the CLI answers with a usage error (exit 64)."""
        backend = _backend({"system_properties": True})
        rec.exit_code = 64
        with pytest.raises(CapabilityError) as excinfo:
            await backend.system_property_get("build.cpus")
        assert excinfo.value.details["feature"] == "system_property_get"
        assert excinfo.value.details["container_version"] == "1.3.1"
        (cmd, _), = rec.calls
        assert cmd == ("system", "property", "get", "build.cpus")

    async def test_set_unavailable_on_131_host(self, rec: _RecordingCli) -> None:
        backend = _backend({"system_properties": True})
        rec.exit_code = 64
        with pytest.raises(CapabilityError) as excinfo:
            await backend.system_property_set("build.cpus", "4")
        assert excinfo.value.details["feature"] == "system_property_set"
        (cmd, _), = rec.calls
        assert cmd == ("system", "property", "set", "build.cpus", "4")

    async def test_get_returns_value_when_host_supports_it(
        self, rec: _RecordingCli,
    ) -> None:
        backend = _backend({"system_properties": True})
        rec.stdout = "2\n"
        assert await backend.system_property_get("build.cpus") == "2"
        (cmd, _), = rec.calls
        assert cmd == ("system", "property", "get", "build.cpus")


class TestSystemVersionWire:
    async def test_command_and_parse(self, rec: _RecordingCli) -> None:
        backend = _backend()
        rec.stdout = SYSTEM_VERSION_JSON
        result = await backend.system_version()
        assert result["container"]["version"] == "1.3.1"
        (cmd, _), = rec.calls
        assert cmd == ("system", "version", "--format", "json")


class TestSystemDfWire:
    async def test_json_path_merges_host_volume_fields(
        self, rec: _RecordingCli, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = _backend()
        rec.stdout = SYSTEM_DF_JSON

        async def fake_os() -> dict[str, Any]:
            return {
                "total": 1_000_000_000_000, "used": 500,
                "free": 999_999_999_500, "path": "/tmp/container",
            }

        monkeypatch.setattr(backend, "_os_disk_usage", fake_os)
        result = await backend.system_df()
        (cmd, _), = rec.calls
        assert cmd == ("system", "df", "--format", "json")
        # runtime store usage from the JSON…
        assert result["used"] == 15459885056
        assert result["store_bytes"] == 15459885056
        assert result["source"] == "container system df --format json"
        # …host-volume total/free (the df JSON has no free-space figure)…
        assert result["total"] == 1_000_000_000_000
        assert result["free"] == 999_999_999_500
        assert result["path"] == "/tmp/container"
        # …and the raw per-category breakdown preserved.
        assert result["images"]["sizeInBytes"] == 15177728000
        assert result["containers"]["active"] == 0

    async def test_no_json_falls_back_to_os_measurement(
        self, rec: _RecordingCli, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = _backend()
        rec.stdout = ""  # host df emits no JSON

        async def fake_os() -> dict[str, Any]:
            return {
                "total": 1_000_000_000_000, "used": 500,
                "free": 999_999_999_500, "path": "/tmp/container",
                "store_bytes": 42,
            }

        monkeypatch.setattr(backend, "_os_disk_usage", fake_os)
        result = await backend.system_df()
        assert result == {
            "total": 1_000_000_000_000, "used": 500,
            "free": 999_999_999_500, "path": "/tmp/container",
            "store_bytes": 42,
        }
