# Copyright (C) 2025, RTE (http://www.rte-france.com)
# SPDX-License-Identifier: Apache-2.0

import logging

import pytest

from setup_ovs import helpers, ovs


DPDK_PORT = {"name": "dpdk0", "type": "dpdk", "interface": "0000:3b:00.0"}

NET_ROOT = "/sys/class/net"
PCI_ROOT = "/sys/bus/pci"


@pytest.fixture
def net_isdir(monkeypatch):
    """
    Answer os.path.isdir for /sys/class/net lookups only.

    ovs.os is the stdlib os module, so the patch is global: anything outside
    the prefix under test must keep the real behaviour or pytest's own
    internals break.
    """
    real_isdir = ovs.os.path.isdir

    def configure(answer):
        def fake_isdir(path):
            if str(path).startswith(NET_ROOT):
                return answer
            return real_isdir(path)

        monkeypatch.setattr(ovs.os.path, "isdir", fake_isdir)

    return configure


@pytest.fixture
def devbind(monkeypatch):
    """Make find_command resolve dpdk-devbind without touching the system."""
    monkeypatch.setattr(
        helpers, "find_command", lambda name, *c: "/usr/bin/dpdk-devbind.py"
    )
    return "/usr/bin/dpdk-devbind.py"


def one_bridge(*ports, **bridge_extra):
    bridge = {"name": "br0"}
    if ports:
        bridge["ports"] = list(ports)
    bridge.update(bridge_extra)
    return {"bridges": [bridge]}


class TestSetupOvsToplevel:
    def test_does_nothing_without_bridges_key(self, run_command):
        ovs.setup_ovs({})

        assert run_command.calls == []

    def test_does_nothing_with_empty_bridges(self, run_command):
        ovs.setup_ovs({"bridges": []})

        assert run_command.calls == []

    def test_disables_dpdk_when_no_dpdk_port(self, run_command):
        ovs.setup_ovs(one_bridge({"name": "p0", "type": "internal"}))

        assert run_command.commands_containing("dpdk-init=false")
        assert run_command.commands_containing("vhost-iommu-support=false")
        assert not run_command.commands_containing("dpdk-init=true")

    def test_enables_dpdk_when_a_dpdk_port_exists(self, run_command, devbind):
        ovs.setup_ovs(one_bridge(DPDK_PORT))

        assert run_command.commands_containing("dpdk-init=true")
        assert run_command.commands_containing("vhost-iommu-support=true")

    def test_binds_every_dpdk_nic(self, run_command, devbind):
        config = one_bridge(
            DPDK_PORT,
            {"name": "dpdk1", "type": "dpdk", "interface": "0000:3b:00.1"},
        )

        ovs.setup_ovs(config)

        bind_calls = run_command.commands_containing("--bind=vfio-pci")
        assert len(bind_calls) == 2
        assert any("0000:3b:00.0" in call for call in bind_calls)
        assert any("0000:3b:00.1" in call for call in bind_calls)

    def test_does_not_look_for_devbind_without_dpdk_port(
        self, run_command, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise AssertionError("find_command must not be called")

        monkeypatch.setattr(helpers, "find_command", boom)

        ovs.setup_ovs(one_bridge({"name": "p0", "type": "internal"}))

    def test_bridge_without_ports_is_created(self, run_command):
        ovs.setup_ovs(one_bridge())

        assert run_command.commands_containing("add-br br0")


class TestCreateBridges:
    def test_plain_bridge_uses_a_simple_add_br(self, run_command):
        ovs.setup_ovs(one_bridge({"name": "p0", "type": "internal"}))

        assert "/usr/bin/ovs-vsctl add-br br0" in run_command.commands

    def test_dpdk_bridge_gets_the_netdev_datapath(self, run_command, devbind):
        ovs.setup_ovs(one_bridge(DPDK_PORT))

        assert run_command.commands_containing("datapath_type=netdev")

    def test_tap_port_creates_the_tun_interface(self, run_command, net_isdir):
        net_isdir(False)

        ovs.setup_ovs(one_bridge({"name": "tap0", "type": "tap"}))

        assert run_command.commands_containing("tuntap add mode tap name tap0")
        assert run_command.commands_containing("link set tap0 up")

    def test_existing_tap_interface_is_not_recreated(
        self, run_command, net_isdir
    ):
        net_isdir(True)

        ovs.setup_ovs(one_bridge({"name": "tap0", "type": "tap"}))

        assert not run_command.commands_containing("tuntap add")
        assert run_command.commands_containing("link set tap0 up")

    def test_system_port_is_named_after_its_interface(self, run_command):
        ovs.setup_ovs(
            one_bridge({"name": "p0", "type": "system", "interface": "eth0"})
        )

        assert run_command.commands_containing("add-port br0 eth0")

    def test_system_port_gets_no_type_option(self, run_command):
        ovs.setup_ovs(
            one_bridge({"name": "p0", "type": "system", "interface": "eth0"})
        )

        assert not run_command.commands_containing("type=system")

    def test_internal_port_gets_its_type_option(self, run_command):
        ovs.setup_ovs(one_bridge({"name": "p0", "type": "internal"}))

        assert run_command.commands_containing("type=internal")

    def test_dpdk_port_carries_the_devargs(self, run_command, devbind):
        ovs.setup_ovs(one_bridge(DPDK_PORT))

        assert run_command.commands_containing(
            "options:dpdk-devargs=0000:3b:00.0"
        )

    def test_vhostuserclient_port_gets_a_socket_path(self, run_command):
        ovs.setup_ovs(one_bridge({"name": "vhost0", "type": "dpdkvhostuserclient"}))

        assert run_command.commands_containing(
            "options:vhost-server-path=/var/run/openvswitch/"
            "vm-sockets/dpdkvhostuser_vhost0"
        )

    def test_vxlan_port_carries_remote_ip_and_key(self, run_command):
        ovs.setup_ovs(
            one_bridge(
                {
                    "name": "vx0",
                    "type": "vxlan",
                    "remote_ip": "10.0.0.1",
                    "key": "42",
                }
            )
        )

        assert run_command.commands_containing("options:remote_ip=10.0.0.1")
        assert run_command.commands_containing("options:key=42")

    def test_vxlan_remote_port_is_optional(self, run_command):
        ovs.setup_ovs(
            one_bridge(
                {
                    "name": "vx0",
                    "type": "vxlan",
                    "remote_ip": "10.0.0.1",
                    "key": "42",
                    "remote_port": "4789",
                }
            )
        )

        assert run_command.commands_containing("options:remote_port=4789")

    @pytest.mark.parametrize(
        "attribute,value,expected",
        [
            ("vlan_mode", "access", "vlan_mode=access"),
            ("tag", 10, "tag=10"),
            ("trunks", [1, 2], "trunks=1,2"),
            ("ofport_request", 7, "ofport_request=7"),
        ],
    )
    def test_optional_port_attributes(
        self, run_command, attribute, value, expected
    ):
        ovs.setup_ovs(
            one_bridge({"name": "p0", "type": "internal", attribute: value})
        )

        assert run_command.commands_containing(expected)

    def test_port_external_ids_accepts_a_string(self, run_command):
        ovs.setup_ovs(
            one_bridge(
                {"name": "p0", "type": "internal", "external-ids": "a=b"}
            )
        )

        assert run_command.commands_containing("external-ids:a=b")

    def test_port_external_ids_accepts_a_list(self, run_command):
        ovs.setup_ovs(
            one_bridge(
                {
                    "name": "p0",
                    "type": "internal",
                    "external-ids": ["a=b", "c=d"],
                }
            )
        )

        assert run_command.commands_containing("external-ids:a=b")
        assert run_command.commands_containing("external-ids:c=d")

    def test_port_other_config_accepts_a_string(self, run_command):
        ovs.setup_ovs(
            one_bridge({"name": "p0", "type": "internal", "other_config": "a=b"})
        )

        assert run_command.commands_containing("set Port p0 other_config=a=b")

    def test_port_other_config_accepts_a_list(self, run_command):
        ovs.setup_ovs(
            one_bridge(
                {
                    "name": "p0",
                    "type": "internal",
                    "other_config": ["a=b", "c=d"],
                }
            )
        )

        assert len(run_command.commands_containing("set Port p0")) == 2

    @pytest.mark.parametrize(
        "attribute", ["ingress_policing_rate", "ingress_policing_burst"]
    )
    def test_ingress_policing_is_applied(self, run_command, attribute):
        ovs.setup_ovs(
            one_bridge({"name": "p0", "type": "internal", attribute: 1000})
        )

        assert run_command.commands_containing("{}=1000".format(attribute))

    def test_hook_file_is_called_with_bridge_and_port(self, run_command):
        ovs.setup_ovs(
            one_bridge(
                {"name": "p0", "type": "internal", "hook_file": "/opt/hook.sh"}
            )
        )

        assert "/opt/hook.sh br0 p0" in run_command.commands

    def test_rstp_is_enabled_when_requested(self, run_command):
        ovs.setup_ovs(one_bridge(rstp_enable=True))

        assert run_command.commands_containing("rstp_enable=true")

    def test_rstp_is_skipped_when_false(self, run_command):
        ovs.setup_ovs(one_bridge(rstp_enable=False))

        assert not run_command.commands_containing("rstp_enable=true")

    def test_bridge_other_config_accepts_a_string(self, run_command):
        ovs.setup_ovs(one_bridge(other_config="a=b"))

        assert run_command.commands_containing("set Bridge br0 other_config=a=b")

    def test_bridge_other_config_accepts_a_list(self, run_command):
        ovs.setup_ovs(one_bridge(other_config=["a=b", "c=d"]))

        assert len(run_command.commands_containing("set Bridge br0")) == 2

    def test_extra_ovsvsctl_command_is_split_on_spaces(self, run_command):
        ovs.setup_ovs(one_bridge(ovsvsctl_extra_cmds="set Bridge br0 stp_enable=true"))

        assert (
            "/usr/bin/ovs-vsctl set Bridge br0 stp_enable=true"
            in run_command.commands
        )

    def test_extra_ovsvsctl_commands_accept_a_list(self, run_command):
        ovs.setup_ovs(
            one_bridge(ovsvsctl_extra_cmds=["set A b", "set C d"])
        )

        assert "/usr/bin/ovs-vsctl set A b" in run_command.commands
        assert "/usr/bin/ovs-vsctl set C d" in run_command.commands


class TestClearOvs:
    def test_deletes_every_listed_bridge(self, run_command, monkeypatch):
        class Result:
            stdout = b"br0\nbr1\n"

        monkeypatch.setattr(
            helpers,
            "run_command",
            lambda *a, **k: Result() if "list-br" in a else run_command(*a, **k),
        )

        ovs.clear_ovs({})

        assert run_command.commands_containing("del-br br0")
        assert run_command.commands_containing("del-br br1")

    def test_keeps_ignored_bridges(self, run_command, monkeypatch):
        class Result:
            stdout = b"br0\nbr1\n"

        monkeypatch.setattr(
            helpers,
            "run_command",
            lambda *a, **k: Result() if "list-br" in a else run_command(*a, **k),
        )

        ovs.clear_ovs({"ignored_bridges": ["br1"]})

        assert run_command.commands_containing("del-br br0")
        assert not run_command.commands_containing("del-br br1")

    def test_skips_blank_lines_in_the_bridge_list(self, run_command, monkeypatch):
        """A blank line between two names must not become a del-br target."""

        class Result:
            stdout = b"br0\n\nbr1\n"

        monkeypatch.setattr(
            helpers,
            "run_command",
            lambda *a, **k: Result() if "list-br" in a else run_command(*a, **k),
        )

        ovs.clear_ovs({})

        assert len(run_command.commands_containing("del-br")) == 2
        assert run_command.commands_containing("del-br br0")
        assert run_command.commands_containing("del-br br1")

    def test_ignored_bridge_that_is_not_up_is_harmless(
        self, run_command, monkeypatch
    ):
        """An ignored_bridges entry naming an absent bridge must be a no-op."""

        class Result:
            stdout = b"br0\n"

        monkeypatch.setattr(
            helpers,
            "run_command",
            lambda *a, **k: Result() if "list-br" in a else run_command(*a, **k),
        )

        ovs.clear_ovs({"ignored_bridges": ["br-absent"]})

        assert run_command.commands_containing("del-br br0")

    def test_handles_an_empty_bridge_list(self, run_command, monkeypatch):
        class Result:
            stdout = b""

        monkeypatch.setattr(
            helpers,
            "run_command",
            lambda *a, **k: Result() if "list-br" in a else run_command(*a, **k),
        )

        ovs.clear_ovs({})

        assert run_command.calls == []

    def test_deletes_nothing_in_dry_run(self, run_command, monkeypatch):
        monkeypatch.setattr(helpers, "dry_run", True)

        ovs.clear_ovs({})

        assert run_command.calls == []


class TestClearTap:
    @pytest.fixture
    def net_devices(self, monkeypatch):
        real_listdir = ovs.os.listdir
        real_isfile = ovs.os.path.isfile

        def configure(names, tap_names):
            def fake_listdir(path):
                if str(path).startswith(NET_ROOT):
                    return names
                return real_listdir(path)

            def fake_isfile(path):
                if str(path).startswith(NET_ROOT):
                    return any(
                        "/{}/tun_flags".format(tap) in str(path)
                        for tap in tap_names
                    )
                return real_isfile(path)

            monkeypatch.setattr(ovs.os, "listdir", fake_listdir)
            monkeypatch.setattr(ovs.os.path, "isfile", fake_isfile)

        return configure

    def test_removes_tap_interfaces_only(self, run_command, net_devices):
        net_devices(["eth0", "tap0", "tap1"], ["tap0", "tap1"])

        ovs.clear_tap({})

        assert run_command.commands_containing("name tap0")
        assert run_command.commands_containing("name tap1")
        assert not run_command.commands_containing("name eth0")

    def test_keeps_ignored_taps(self, run_command, net_devices):
        net_devices(["tap0", "tap1"], ["tap0", "tap1"])

        ovs.clear_tap({"ignored_taps": ["tap1"]})

        assert run_command.commands_containing("name tap0")
        assert not run_command.commands_containing("name tap1")

    def test_empty_ignored_taps_is_ignored(self, run_command, net_devices):
        net_devices(["tap0"], ["tap0"])

        ovs.clear_tap({"ignored_taps": []})

        assert run_command.commands_containing("name tap0")


class TestUnbindPci:
    @pytest.fixture
    def sysfs(self, monkeypatch):
        """
        Simulate /sys/bus/pci for unbind_pci.

        os.path.exists and builtins.open are global, so every path outside
        /sys/bus/pci keeps its real behaviour.
        """
        writes = []
        real_exists = ovs.os.path.exists
        real_open = open

        def configure(existing, driver):
            def fake_exists(path):
                path = str(path)
                if not path.startswith(PCI_ROOT):
                    return real_exists(path)
                if path.endswith("/driver"):
                    return existing and driver is not None
                return existing

            monkeypatch.setattr(ovs.os.path, "exists", fake_exists)
            monkeypatch.setattr(
                ovs.os,
                "readlink",
                lambda path: "../../../bus/pci/drivers/" + (driver or ""),
            )

            class FakeFile:
                def __init__(self, path):
                    self.path = path

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def write(self, data):
                    writes.append((self.path, data))

            def fake_open(path, mode="r", *args, **kwargs):
                if str(path).startswith(PCI_ROOT):
                    return FakeFile(str(path))
                return real_open(path, mode, *args, **kwargs)

            monkeypatch.setattr("builtins.open", fake_open)

        configure.writes = writes
        return configure

    def test_does_nothing_without_the_key(self, sysfs):
        sysfs(existing=True, driver="ixgbe")

        ovs.unbind_pci({})

        assert sysfs.writes == []

    def test_unbinds_a_bound_device(self, sysfs):
        sysfs(existing=True, driver="ixgbe")

        ovs.unbind_pci({"unbind_pci_address": ["0000:3b:00.0"]})

        assert sysfs.writes == [
            ("/sys/bus/pci/drivers/ixgbe/unbind", "0000:3b:00.0")
        ]

    def test_normalises_a_short_pci_address(self, sysfs):
        sysfs(existing=True, driver="ixgbe")

        ovs.unbind_pci({"unbind_pci_address": ["3:0.0"]})

        assert sysfs.writes == [
            ("/sys/bus/pci/drivers/ixgbe/unbind", "0000:03:00.0")
        ]

    def test_skips_an_absent_device(self, sysfs, caplog):
        sysfs(existing=False, driver="ixgbe")

        ovs.unbind_pci({"unbind_pci_address": ["0000:3b:00.0"]})

        assert sysfs.writes == []
        assert "not found" in caplog.text

    def test_skips_a_device_without_driver(self, sysfs, caplog):
        caplog.set_level(logging.INFO)
        sysfs(existing=True, driver=None)

        ovs.unbind_pci({"unbind_pci_address": ["0000:3b:00.0"]})

        assert sysfs.writes == []
        assert "no driver to bind" in caplog.text

    def test_skips_a_device_already_on_vfio(self, sysfs, caplog):
        caplog.set_level(logging.INFO)
        sysfs(existing=True, driver="vfio_pci")

        ovs.unbind_pci({"unbind_pci_address": ["0000:3b:00.0"]})

        assert sysfs.writes == []
        assert "already bound" in caplog.text


class TestBindDpdkInterfaces:
    def test_uses_the_first_available_devbind(self, run_command, monkeypatch):
        seen = {}

        def fake_find(name, *candidates):
            seen["candidates"] = candidates
            return "/usr/sbin/dpdk-devbind"

        monkeypatch.setattr(helpers, "find_command", fake_find)

        ovs._bind_dpdk_interfaces(["0000:3b:00.0"])

        assert seen["candidates"] == ovs._DPDK_DEVBIND_CANDIDATES
        assert run_command.commands == [
            "/usr/sbin/dpdk-devbind --force --bind=vfio-pci 0000:3b:00.0"
        ]

    def test_propagates_a_missing_devbind(self, monkeypatch):
        def fake_find(name, *candidates):
            raise FileNotFoundError("nope")

        monkeypatch.setattr(helpers, "find_command", fake_find)

        with pytest.raises(FileNotFoundError):
            ovs._bind_dpdk_interfaces(["0000:3b:00.0"])
