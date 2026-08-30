"""Unit tests for the L1 output parsers and models (no daemon required).

The parsers are pure static methods on ``OutputParser`` and the rate
math lives on the models, so these tests run against the literal wire
shapes captured in SPEC §2 on container 1.2.2.
"""

from __future__ import annotations

import pytest

from daedalus.core.backend import (
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
